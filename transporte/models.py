"""
MÓDULO DE MODELOS (transporte/models.py)
Diseño relacional en Tercera Forma Normal (3NF) para el sistema AndesSur.
Incluye integridad referencial, atributos CHOICES, ciclo de vida de órdenes
y persistencia de carro en PostgreSQL.
"""

from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
import re

# ==============================================================================
# VALIDACIÓN DE NEGOCIO (RUT CHILENO - MÓDULO 11)
# ==============================================================================
def validar_rut_chileno(rut):
    """Valida el formato y el dígito verificador mediante algoritmo Módulo 11."""
    rut_limpio = rut.replace('.', '').replace('-', '').upper().strip()
    if not re.match(r'^\d{7,8}[0-9K]$', rut_limpio):
        raise ValidationError('Formato de RUT no válido (ej: 12.345.678-9 o 12345678-K).')
    
    cuerpo = rut_limpio[:-1]
    dv = rut_limpio[-1]
    
    suma = 0
    multiplicador = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1
        
    resto = 11 - (suma % 11)
    dv_esperado = 'K' if resto == 10 else ('0' if resto == 11 else str(resto))
    
    if dv != dv_esperado:
        raise ValidationError('Dígito verificador de RUT no válido.')

# ==============================================================================
# PERFILES Y ROLES DE USUARIO (RBAC)
# ==============================================================================
class PerfilUsuario(models.Model):
    ROLES = [
        ('PASAJERO', 'Pasajero'),
        ('ADMIN_FLOTA', 'Administrador de Flota'),
    ]
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    rol = models.CharField(max_length=20, choices=ROLES, default='PASAJERO')
    telefono = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.usuario.username} - {self.rol}"

# ==============================================================================
# ENTIDADES MAESTRAS (3NF)
# ==============================================================================
class Ciudad(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.nombre

class Bus(models.Model):
    patente = models.CharField(max_length=10, unique=True)

    def __str__(self):
        return f"Bus {self.patente}"

class Asiento(models.Model):
    TIPO_CHOICES = [
        ('SEMICAMA', 'Semicama'),
        ('SALON_CAMA', 'Salón Cama'),
    ]
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name='asientos')
    numero = models.PositiveIntegerField()
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='SEMICAMA')

    class Meta:
        unique_together = ('bus', 'numero')

    def __str__(self):
        return f"Asiento #{self.numero} ({self.get_tipo_display()}) - {self.bus.patente}"

    def calcular_precio(self, precio_base):
        """Salón Cama aplica 20% de recargo sobre precio base."""
        if self.tipo == 'SALON_CAMA':
            return int(precio_base * 1.20)
        return int(precio_base)

class Servicio(models.Model):
    origen = models.ForeignKey(Ciudad, on_delete=models.PROTECT, related_name='salidas')
    destino = models.ForeignKey(Ciudad, on_delete=models.PROTECT, related_name='llegadas')
    bus = models.ForeignKey(Bus, on_delete=models.PROTECT, related_name='servicios')
    fecha_salida = models.DateTimeField()
    precio_base = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.origen} ➔ {self.destino} ({self.fecha_salida.strftime('%d/%m/%Y %H:%M')})"

# ==============================================================================
# CARRO PERSISTENTE (POSTGRESQL 1:1)
# ==============================================================================
class CarroPasajes(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='carro')
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Carro de {self.usuario.username}"

class ItemCarro(models.Model):
    carro = models.ForeignKey(CarroPasajes, on_delete=models.CASCADE, related_name='items')
    servicio = models.ForeignKey(Servicio, on_delete=models.CASCADE)
    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE)
    nombre_ocupante = models.CharField(max_length=150)
    rut_ocupante = models.CharField(max_length=15)
    agregado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('carro', 'servicio', 'asiento')

    def obtener_subtotal(self):
        return self.asiento.calcular_precio(self.servicio.precio_base)

# ==============================================================================
# CICLO DE VIDA DE TRANSACCIONES Y ÓRDENES
# ==============================================================================
class Venta(models.Model):
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('PAGADO', 'Pagado'),
        ('ENTREGADO', 'Entregado / Completado'),
        ('CANCELADO', 'Cancelado'),
    ]
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, related_name='ventas')
    fecha_venta = models.DateTimeField(auto_now_add=True)
    total = models.PositiveIntegerField()
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')

    def __str__(self):
        return f"Venta #{self.id} - {self.usuario.username} (${self.total}) [{self.estado}]"

class Boleto(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='boletos')
    servicio = models.ForeignKey(Servicio, on_delete=models.PROTECT, related_name='boletos')
    asiento = models.ForeignKey(Asiento, on_delete=models.PROTECT, related_name='boletos')
    nombre_pasajero = models.CharField(max_length=150)
    rut_pasajero = models.CharField(max_length=15)
    precio_pagado = models.PositiveIntegerField()

    class Meta:
        unique_together = ('servicio', 'asiento', 'venta')

    def __str__(self):
        return f"Boleto #{self.id} - Asiento {self.asiento.numero} ({self.nombre_pasajero})"