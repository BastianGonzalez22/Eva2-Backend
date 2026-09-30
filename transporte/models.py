"""
==============================================================================
MÓDULO DE MODELOS DE DOMINIO Y PERSISTENCIA (transporte/models.py)
------------------------------------------------------------------------------
Define las entidades estructurales para el sistema de venta de pasajes AndesSur:
- Control de perfiles y roles de usuario (RBAC) con campo usuario.
- Infraestructura de transporte: Ciudades, Buses y Butacas configurables.
- Itinerarios y programación de Recorridos (Servicios).
- Carro de compras persistente con marcas de tiempo (agregado_en, actualizado_en).
- Cabecera de Ventas con estados y Boletos emitidos con restricción de unicidad.
- Protección referencial con on_delete=models.PROTECT en ventas y servicios.
- Señal post_save para creación automática de perfiles.
==============================================================================
"""

import re
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver


# ==============================================================================
# VALIDACIÓN DE FORMATO E INTEGRIDAD
# ==============================================================================
def validar_rut_chileno(rut_str):
    """
    Valida el formato y el dígito verificador de un RUT chileno usando el algoritmo Módulo 11.
    Acepta formatos con o sin puntos y con guion (ej: 12.345.678-5 o 12345678-5).
    Lanza ValidationError si el formato es inválido o el dígito verificador no coincide.
    """
    rut_limpio = re.sub(r'[^0-9kK]', '', str(rut_str)).upper()
    if len(rut_limpio) < 2:
        raise ValidationError('El RUT ingresado no posee un largo suficiente.')

    cuerpo = rut_limpio[:-1]
    dv_ingresado = rut_limpio[-1]

    if not cuerpo.isdigit():
        raise ValidationError('El cuerpo del RUT debe contener únicamente dígitos numéricos.')

    suma = 0
    multiplicador = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1

    resto = suma % 11
    resultado = 11 - resto
    if resultado == 11:
        dv_esperado = '0'
    elif resultado == 10:
        dv_esperado = 'K'
    else:
        dv_esperado = str(resultado)

    if dv_ingresado != dv_esperado:
        raise ValidationError(f'El dígito verificador del RUT ({dv_ingresado}) no es válido.')


# ==============================================================================
# MODELO: PERFIL DE USUARIO Y CONTROL DE ROLES (RBAC)
# ==============================================================================
class PerfilUsuario(models.Model):
    """
    Extensión del modelo User de Django mediante relación OneToOne.
    Almacena el rol RBAC (ADMIN_FLOTA o PASAJERO) y datos de contacto complementarios.
    """
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
    telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Teléfono de Contacto'
    )

    class Meta:
        verbose_name = 'Perfil de Usuario'
        verbose_name_plural = 'Perfiles de Usuario'

    def __str__(self):
        return f'{self.usuario.username} - {self.get_rol_display()}'


# ==============================================================================
# MODELO: CIUDADES Y TERMINALES
# ==============================================================================
class Ciudad(models.Model):
    """
    Representa una localidad o terminal de buses origen o destino.
    """
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
    """
    Unidad de transporte vehicular identificada por su placa patente.
    """
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
# MODELO: ASIENTOS / BUTACAS
# ==============================================================================
class Asiento(models.Model):
    """
    Butaca física dentro de un bus. Aplica recargo de 20% si es SALON_CAMA.
    """
    TIPOS = [
        ('SEMICAMA', 'Semicama'),
        ('SALON_CAMA', 'Salón Cama'),
    ]

    bus = models.ForeignKey(
        Bus,
        on_delete=models.CASCADE,
        related_name='asientos',
        verbose_name='Bus Asignado'
    )
    numero = models.PositiveIntegerField(
        verbose_name='Número de Butaca'
    )
    tipo = models.CharField(
        max_length=20,
        choices=TIPOS,
        default='SEMICAMA',
        verbose_name='Tipo de Asiento'
    )

    class Meta:
        verbose_name = 'Asiento'
        verbose_name_plural = 'Asientos'
        unique_together = ('bus', 'numero')
        ordering = ['numero']

    def __str__(self):
        return f'Asiento {self.numero} ({self.get_tipo_display()}) - Bus {self.bus.patente}'

    def calcular_precio(self, precio_base):
        """Calcula el valor según la categoría del asiento."""
        if self.tipo == 'SALON_CAMA':
            return int(precio_base * 1.20)
        return int(precio_base)


# ==============================================================================
# MODELO: SERVICIOS / ITINERARIOS
# ==============================================================================
class Servicio(models.Model):
    """
    Recorrido programado entre dos ciudades con bus y tarifa base asignada.
    """
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
    """
    Carro de compras persistente vinculado al usuario.
    """
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
    """
    Pasaje temporal dentro del carro de compras.
    """
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
        max_length=150,
        verbose_name='Nombre del Ocupante'
    )
    rut_ocupante = models.CharField(
        max_length=12,
        verbose_name='RUT del Ocupante'
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
        return f'Asiento {self.asiento.numero} para {self.nombre_ocupante}'

    def obtener_subtotal(self):
        """Retorna subtotal según tarifa base y butaca."""
        return self.asiento.calcular_precio(self.servicio.precio_base)


# ==============================================================================
# MODELO: CABECERA DE VENTAS
# ==============================================================================
class Venta(models.Model):
    """
    Cabecera transaccional con ciclo de vida:
    PENDIENTE -> PAGADO -> ENTREGADO / CANCELADO.
    """
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
# MODELO: BOLETOS DE PASAJE EMITIDOS
# ==============================================================================
class Boleto(models.Model):
    """
    Título de transporte individual emitido asociado a una venta.
    """
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
        max_length=150,
        verbose_name='Nombre del Pasajero'
    )
    rut_pasajero = models.CharField(
        max_length=12,
        verbose_name='RUT del Pasajero'
    )
    precio_pagado = models.PositiveIntegerField(
        verbose_name='Precio Pagado'
    )

    class Meta:
        verbose_name = 'Boleto'
        verbose_name_plural = 'Boletos'
        unique_together = ('servicio', 'asiento', 'venta')

    def __str__(self):
        return f'Boleto #{self.id} - Asiento {self.asiento.numero} ({self.nombre_pasajero})'


# ==============================================================================
# SEÑALES DE DISPATCH (SIGNALS)
# ==============================================================================
@receiver(post_save, sender=User)
def crear_perfil_usuario_automatico(sender, instance, created, **kwargs):
    """
    Crea automáticamente PerfilUsuario con rol PASAJERO al registrar un usuario.
    """
    if created:
        PerfilUsuario.objects.get_or_create(usuario=instance)