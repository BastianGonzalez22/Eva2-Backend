"""
==============================================================================
MÓDULO DE MODELOS DE DOMINIO Y PERSISTENCIA (transporte/models.py)
------------------------------------------------------------------------------
Define las entidades estructurales para el sistema de venta de pasajes AndesSur:
- Control de perfiles y roles de usuario (RBAC).
- Infraestructura de transporte: Ciudades, Buses y Butacas configurables.
- Itinerarios y programación de Recorridos (Servicios).
- Carro de compras persistente por usuario y desglose de ítems.
- Cabecera de Ventas con ciclo de vida de estados y Boletos emitidos.
- Validadores de integridad nacional (RUT chileno mediante Módulo 11).
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

    # Algoritmo de ponderación Módulo 11
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
    Almacena atributos de negocio adicionales, como el rol asignado dentro
    del esquema de control de accesos basados en roles (RBAC).
    """
    ROLES = [
        ('ADMIN_FLOTA', 'Administrador de Flota'),
        ('PASAJERO', 'Pasajero Cliente'),
    ]

    user = models.OneToOneField(
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

    class Meta:
        verbose_name = 'Perfil de Usuario'
        verbose_name_plural = 'Perfiles de Usuario'

    def __str__(self):
        """Representación textual del perfil del usuario."""
        return f'{self.user.username} - {self.get_rol_display()}'


# ==============================================================================
# MODELO: CIUDADES Y TERMINALES
# ==============================================================================
class Ciudad(models.Model):
    """
    Representa una localidad geográfica o terminal que actúa como origen
    o destino dentro de los itinerarios de transporte interurbano.
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
        """Retorna el nombre descriptivo de la ciudad."""
        return self.nombre


# ==============================================================================
# MODELO: BUSES
# ==============================================================================
class Bus(models.Model):
    """
    Representa una unidad física de transporte (vehículo) identificada
    por su patente única. Contiene la colección de butacas disponibles.
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
        """Retorna la patente vehicular asociada al bus."""
        return f'Bus [{self.patente}]'


# ==============================================================================
# MODELO: ASIENTOS / BUTACAS
# ==============================================================================
class Asiento(models.Model):
    """
    Representa una butaca numerada perteneciente a un bus en particular.
    Define la categoría de confort (SEMICAMA o SALON_CAMA), la cual influye
    directamente en el cálculo final de la tarifa del pasaje.
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
        """Retorna el número de asiento, bus y su clasificación."""
        return f'Asiento {self.numero} ({self.get_tipo_display()}) - Bus {self.bus.patente}'

    def calcular_precio(self, precio_base):
        """
        Calcula el valor del asiento en base a su categoría.
        Aplica un recargo del 20% sobre el precio base si la butaca es SALON_CAMA.
        """
        if self.tipo == 'SALON_CAMA':
            return int(precio_base * 1.20)
        return int(precio_base)


# ==============================================================================
# MODELO: SERVICIOS / ITINERARIOS
# ==============================================================================
class Servicio(models.Model):
    """
    Define un recorrido programado en una fecha y horario específicos,
    asociando una ciudad de origen, una ciudad de destino y un bus asignado.
    """
    origen = models.ForeignKey(
        Ciudad,
        on_delete=models.CASCADE,
        related_name='servicios_origen',
        verbose_name='Ciudad de Origen'
    )
    destino = models.ForeignKey(
        Ciudad,
        on_delete=models.CASCADE,
        related_name='servicios_destino',
        verbose_name='Ciudad de Destino'
    )
    bus = models.ForeignKey(
        Bus,
        on_delete=models.CASCADE,
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
        """Representación textual del recorrido, itinerario y horario."""
        return f'{self.origen.nombre} -> {self.destino.nombre} ({self.fecha_salida.strftime("%d/%m/%Y %H:%M")})'


# ==============================================================================
# MODELO: CARRO DE COMPRAS DE PASAJES
# ==============================================================================
class CarroPasajes(models.Model):
    """
    Entidad persistente vinculada de manera unívoca a un usuario.
    Alberga de forma temporal la selección de pasajes antes de proceder al checkout.
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

    class Meta:
        verbose_name = 'Carro de Pasajes'
        verbose_name_plural = 'Carros de Pasajes'

    def __str__(self):
        """Muestra el usuario propietario del carro de compras."""
        return f'Carro de {self.usuario.username}'


# ==============================================================================
# MODELO: ÍTEMS DEL CARRO DE COMPRAS
# ==============================================================================
class ItemCarro(models.Model):
    """
    Representa un pasaje individual agregado al carro de compras.
    Contiene la referencia al servicio, butaca y los datos personales
    del pasajero (nombre y RUT verificado) antes de la emisión definitiva.
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

    class Meta:
        verbose_name = 'Ítem de Carro'
        verbose_name_plural = 'Ítems de Carro'
        unique_together = ('carro', 'servicio', 'asiento')

    def __str__(self):
        """Retorna descripción resumida del pasaje en carro."""
        return f'Asiento {self.asiento.numero} para {self.nombre_ocupante}'

    def obtener_subtotal(self):
        """
        Calcula el valor unitario de este ítem considerando la tarifa base
        del servicio y el tipo de butaca (Semicama o Salón Cama).
        """
        return self.asiento.calcular_precio(self.servicio.precio_base)


# ==============================================================================
# MODELO: CABECERA DE VENTAS / ÓRDENES DE COMPRA
# ==============================================================================
class Venta(models.Model):
    """
    Cabecera transaccional de compras realizadas en la plataforma.
    Gestiona el ciclo de vida de la transacción mediante sus estados:
    - PENDIENTE: Orden generada en espera de confirmación de pago.
    - PAGADO: Transacción pagada con asientos reservados atómicamente.
    - ENTREGADO: Boletos emitidos y validados para el viaje.
    - CANCELADO: Orden anulada liberando butacas al inventario general.
    """
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('PAGADO', 'Pagado'),
        ('ENTREGADO', 'Entregado'),
        ('CANCELADO', 'Cancelado'),
    ]

    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
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
        """Retorna identificador, estado y total pagado de la venta."""
        return f'Venta #{self.id} - {self.usuario.username} [{self.get_estado_display()}]'


# ==============================================================================
# MODELO: BOLETOS DE PASAJE EMITIDOS
# ==============================================================================
class Boleto(models.Model):
    """
    Representa el título de transporte individual emitido tras una compra.
    Asocia un asiento físico irrevocable con los datos del pasajero,
    vinculado a su cabecera de Venta correspondiente.
    """
    venta = models.ForeignKey(
        Venta,
        on_delete=models.CASCADE,
        related_name='boletos',
        verbose_name='Venta Asociada'
    )
    servicio = models.ForeignKey(
        Servicio,
        on_delete=models.CASCADE,
        related_name='boletos_emitidos',
        verbose_name='Servicio de Transporte'
    )
    asiento = models.ForeignKey(
        Asiento,
        on_delete=models.CASCADE,
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

    def __str__(self):
        """Retorna código de boleto, pasajero y butaca asignada."""
        return f'Boleto #{self.id} - Asiento {self.asiento.numero} ({self.nombre_pasajero})'


# ==============================================================================
# SEÑALES DE DISPATCH (SIGNALS)
# ==============================================================================
@receiver(post_save, sender=User)
def crear_perfil_usuario_automatico(sender, instance, created, **kwargs):
    """
    Señal post_save que crea automáticamente una instancia de PerfilUsuario
    con rol PASAJERO cada vez que se registra un nuevo usuario en Django.
    """
    if created:
        PerfilUsuario.objects.get_or_create(user=instance)