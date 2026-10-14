import re
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver


def validar_rut_chileno(rut_str):
    """
    Valida que el RUT tenga un formato chileno válido (números + dígito verificador 0-9 o K).
    """
    rut_limpio = re.sub(r'[^0-9kK]', '', str(rut_str)).upper()
    if len(rut_limpio) < 7 or len(rut_limpio) > 10:
        raise ValidationError('El RUT ingresado debe tener entre 7 y 9 dígitos más el verificador.')

    cuerpo = rut_limpio[:-1]
    dv_ingresado = rut_limpio[-1]

    if not cuerpo.isdigit():
        raise ValidationError('El cuerpo del RUT debe contener únicamente dígitos numéricos.')

    if not (dv_ingresado.isdigit() or dv_ingresado == 'K'):
        raise ValidationError('El dígito verificador debe ser un número del 0 al 9 o la letra K.')


# ==============================================================================
# MODELO: PERFIL DE USUARIO Y CONTROL DE ROLES (RBAC) ENRIQUECIDO
# ==============================================================================
class PerfilUsuario(models.Model):
    ROLES = [
        ('ADMIN_FLOTA', 'Administrador de Flota'),
        ('PASAJERO', 'Pasajero Cliente'),
    ]

    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='perfil',
        verbose_name='Usuario del Sistema'
    )
    rol = models.CharField(
        max_length=20,
        choices=ROLES,
        default='PASAJERO',
        verbose_name='Rol en la Plataforma'
    )
    rut = models.CharField(
        max_length=12,
        blank=True,
        null=True,
        verbose_name='RUT del Pasajero'
    )
    telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Teléfono de Contacto'
    )
    fecha_nacimiento = models.DateField(
        blank=True,
        null=True,
        verbose_name='Fecha de Nacimiento'
    )
    contacto_emergencia_nombre = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Nombre Contacto de Emergencia'
    )
    contacto_emergencia_telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Teléfono de Emergencia'
    )

    class Meta:
        verbose_name = 'Perfil de Usuario'
        verbose_name_plural = 'Perfiles de Usuario'

    def __str__(self):
        return f'{self.usuario.username} ({self.rut or "Sin RUT"}) - {self.get_rol_display()}'


# ==============================================================================
# MODELO: CHOICES DINÁMICOS - TIPO DE ASIENTO (ADMINISTRABLE EN ADMIN Y API)
# ==============================================================================
class TipoAsiento(models.Model):
    codigo = models.CharField(
        max_length=30,
        unique=True,
        verbose_name='Código Interno (ej: SEMICAMA, SALON_CAMA)'
    )
    nombre = models.CharField(
        max_length=50,
        verbose_name='Nombre Visible (ej: Semicama, Salón Cama)'
    )
    multiplicador_precio = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.00'),
        verbose_name='Multiplicador de Tarifa (ej: 1.00 o 1.20)'
    )

    class Meta:
        verbose_name = 'Tipo de Asiento'
        verbose_name_plural = 'Tipos de Asiento'
        ordering = ['multiplicador_precio']

    def __str__(self):
        return f'{self.nombre} (x{self.multiplicador_precio})'


# ==============================================================================
# MODELO: CIUDADES Y TERMINALES
# ==============================================================================
class Ciudad(models.Model):
    nombre = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Nombre de la Ciudad'
    )

    class Meta:
        verbose_name = 'Ciudad'
        verbose_name_plural = 'Ciudades'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


# ==============================================================================
# MODELO: BUSES
# ==============================================================================
class Bus(models.Model):
    patente = models.CharField(
        max_length=10,
        unique=True,
        verbose_name='Patente Vehicular'
    )

    class Meta:
        verbose_name = 'Bus'
        verbose_name_plural = 'Buses'

    def __str__(self):
        return f'Bus [{self.patente}]'


# ==============================================================================
# MODELO: ASIENTOS / BUTACAS (CON TIPO DINÁMICO)
# ==============================================================================
class Asiento(models.Model):
    bus = models.ForeignKey(
        Bus,
        on_delete=models.CASCADE,
        related_name='asientos',
        verbose_name='Bus Asignado'
    )
    numero = models.PositiveIntegerField(
        verbose_name='Número de Butaca'
    )
    tipo_asiento = models.ForeignKey(
        TipoAsiento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='asientos',
        verbose_name='Categoría de Asiento (Dinámico)'
    )

    class Meta:
        verbose_name = 'Asiento'
        verbose_name_plural = 'Asientos'
        unique_together = ('bus', 'numero')
        ordering = ['numero']

    @property
    def tipo(self):
        return self.tipo_asiento.codigo if self.tipo_asiento else 'SEMICAMA'

    def get_tipo_display(self):
        return self.tipo_asiento.nombre if self.tipo_asiento else 'Semicama'

    def __str__(self):
        return f'Asiento {self.numero} ({self.get_tipo_display()}) - Bus {self.bus.patente}'

    def calcular_precio(self, precio_base):
        if self.tipo_asiento:
            return int(Decimal(precio_base) * self.tipo_asiento.multiplicador_precio)
        return int(precio_base)


# ==============================================================================
# MODELO: SERVICIOS / ITINERARIOS
# ==============================================================================
class Servicio(models.Model):
    origen = models.ForeignKey(
        Ciudad,
        on_delete=models.PROTECT,
        related_name='servicios_origen',
        verbose_name='Ciudad de Origen'
    )
    destino = models.ForeignKey(
        Ciudad,
        on_delete=models.PROTECT,
        related_name='servicios_destino',
        verbose_name='Ciudad de Destino'
    )
    bus = models.ForeignKey(
        Bus,
        on_delete=models.PROTECT,
        related_name='servicios',
        verbose_name='Bus Asignado'
    )
    fecha_salida = models.DateTimeField(
        verbose_name='Fecha y Hora de Salida'
    )
    precio_base = models.PositiveIntegerField(
        verbose_name='Precio Base (Semicama)'
    )

    class Meta:
        verbose_name = 'Servicio'
        verbose_name_plural = 'Servicios'
        ordering = ['fecha_salida']

    def __str__(self):
        return f'{self.origen.nombre} -> {self.destino.nombre} ({self.fecha_salida.strftime("%d/%m/%Y %H:%M")})'


# ==============================================================================
# MODELO: CARRO DE COMPRAS DE PASAJES
# ==============================================================================
class CarroPasajes(models.Model):
    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='carro_pasajes',
        verbose_name='Usuario Propietario'
    )
    creado_en = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creación'
    )
    actualizado_en = models.DateTimeField(
        auto_now=True,
        verbose_name='Última Actualización'
    )

    class Meta:
        verbose_name = 'Carro de Pasajes'
        verbose_name_plural = 'Carros de Pasajes'

    def __str__(self):
        return f'Carro de {self.usuario.username}'


# ==============================================================================
# MODELO: ÍTEMS DEL CARRO DE COMPRAS
# ==============================================================================
class ItemCarro(models.Model):
    carro = models.ForeignKey(
        CarroPasajes,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Carro Contenedor'
    )
    servicio = models.ForeignKey(
        Servicio,
        on_delete=models.CASCADE,
        related_name='en_carros',
        verbose_name='Servicio Programado'
    )
    asiento = models.ForeignKey(
        Asiento,
        on_delete=models.CASCADE,
        verbose_name='Butaca Seleccionada'
    )
    nombre_ocupante = models.CharField(
        max_length=100,
        verbose_name='Nombre del Ocupante'
    )
    apellido_ocupante = models.CharField(
        max_length=100,
        default='',
        verbose_name='Apellido del Ocupante'
    )
    rut_ocupante = models.CharField(
        max_length=12,
        verbose_name='RUT del Ocupante'
    )
    es_menor = models.BooleanField(
        default=False,
        verbose_name='¿Es Menor de Edad?'
    )
    agregado_en = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Inclusión'
    )

    class Meta:
        verbose_name = 'Ítem de Carro'
        verbose_name_plural = 'Ítems de Carro'
        unique_together = ('carro', 'servicio', 'asiento')

    def __str__(self):
        return f'Asiento {self.asiento.numero} para {self.nombre_ocupante} {self.apellido_ocupante}'.strip()

    def obtener_subtotal(self):
        return self.asiento.calcular_precio(self.servicio.precio_base)


# ==============================================================================
# MODELO: CABECERA DE VENTAS
# ==============================================================================
class Venta(models.Model):
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('PAGADO', 'Pagado'),
        ('ENTREGADO', 'Entregado'),
        ('CANCELADO', 'Cancelado'),
    ]

    usuario = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='ventas',
        verbose_name='Cliente Comprador'
    )
    fecha_venta = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Emisión'
    )
    total = models.PositiveIntegerField(
        verbose_name='Total de la Transacción'
    )
    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='PENDIENTE',
        verbose_name='Estado de la Orden'
    )

    class Meta:
        verbose_name = 'Venta'
        verbose_name_plural = 'Ventas'
        ordering = ['-fecha_venta']

    def __str__(self):
        return f'Venta #{self.id} - {self.usuario.username} [{self.get_estado_display()}]'


# ==============================================================================
# MODELO: BOLETOS DE PASAJE EMITIDOS (CON DATOS INDIVIDUALES POR PASAJERO)
# ==============================================================================
class Boleto(models.Model):
    venta = models.ForeignKey(
        Venta,
        on_delete=models.CASCADE,
        related_name='boletos',
        verbose_name='Venta Asociada'
    )
    servicio = models.ForeignKey(
        Servicio,
        on_delete=models.PROTECT,
        related_name='boletos_emitidos',
        verbose_name='Servicio de Transporte'
    )
    asiento = models.ForeignKey(
        Asiento,
        on_delete=models.PROTECT,
        verbose_name='Asiento Asignado'
    )
    nombre_pasajero = models.CharField(
        max_length=100,
        verbose_name='Nombre del Pasajero'
    )
    apellido_pasajero = models.CharField(
        max_length=100,
        default='',
        verbose_name='Apellido del Pasajero'
    )
    rut_pasajero = models.CharField(
        max_length=12,
        verbose_name='RUT del Pasajero'
    )
    es_menor = models.BooleanField(
        default=False,
        verbose_name='¿Es Menor de Edad?'
    )
    precio_pagado = models.PositiveIntegerField(
        verbose_name='Precio Pagado'
    )

    class Meta:
        verbose_name = 'Boleto'
        verbose_name_plural = 'Boletos'
        unique_together = ('servicio', 'asiento', 'venta')

    def __str__(self):
        return f'Boleto #{self.id} - Asiento {self.asiento.numero} ({self.nombre_pasajero} {self.apellido_pasajero})'.strip()


# ==============================================================================
# SEÑALES DE DISPATCH (SIGNALS)
# ==============================================================================
@receiver(post_save, sender=User)
def crear_perfil_usuario_automatico(sender, instance, created, **kwargs):
    if created:
        PerfilUsuario.objects.get_or_create(usuario=instance)