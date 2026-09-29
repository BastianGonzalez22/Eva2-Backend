"""
MÓDULO DE SERIALIZADORES (transporte/serializers.py)
Define la representación JSON para DRF y Swagger OpenAPI.
"""

from rest_framework import serializers
from .models import Ciudad, Bus, Asiento, Servicio, Venta, Boleto, ItemCarro, CarroPasajes

# ==============================================================================
# SERIALIZADORES DE CATÁLOGO Y FLOTA
# ==============================================================================
class CiudadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ciudad
        fields = ['id', 'nombre']

class AsientoSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = Asiento
        fields = ['id', 'numero', 'tipo', 'tipo_display']

class BusSerializer(serializers.ModelSerializer):
    asientos = AsientoSerializer(many=True, read_only=True)

    class Meta:
        model = Bus
        fields = ['id', 'patente', 'asientos']

class ServicioSerializer(serializers.ModelSerializer):
    origen = CiudadSerializer(read_only=True)
    destino = CiudadSerializer(read_only=True)
    bus_patente = serializers.CharField(source='bus.patente', read_only=True)

    class Meta:
        model = Servicio
        fields = ['id', 'origen', 'destino', 'bus_patente', 'fecha_salida', 'precio_base']

# ==============================================================================
# SERIALIZADORES DE CARRO Y VENTAS
# ==============================================================================
class ItemCarroSerializer(serializers.ModelSerializer):
    servicio = ServicioSerializer(read_only=True)
    asiento = AsientoSerializer(read_only=True)
    subtotal = serializers.IntegerField(source='obtener_subtotal', read_only=True)

    class Meta:
        model = ItemCarro
        fields = ['id', 'servicio', 'asiento', 'nombre_ocupante', 'rut_ocupante', 'subtotal']

class CarroPasajesSerializer(serializers.ModelSerializer):
    items = ItemCarroSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = CarroPasajes
        fields = ['id', 'items', 'total']

    def get_total(self, obj):
        return sum(item.obtener_subtotal() for item in obj.items.all())

class BoletoSerializer(serializers.ModelSerializer):
    asiento_numero = serializers.IntegerField(source='asiento.numero', read_only=True)

    class Meta:
        model = Boleto
        fields = ['id', 'servicio', 'asiento_numero', 'nombre_pasajero', 'rut_pasajero', 'precio_pagado']

class VentaSerializer(serializers.ModelSerializer):
    boletos = BoletoSerializer(many=True, read_only=True)
    usuario_nombre = serializers.CharField(source='usuario.username', read_only=True)

    class Meta:
        model = Venta
        fields = ['id', 'usuario_nombre', 'fecha_venta', 'total', 'estado', 'boletos']

# Serializadores para documentación y payloads Swagger
class CambiarEstadoSerializer(serializers.Serializer):
    estado = serializers.ChoiceField(choices=['PAGADO', 'ENTREGADO', 'CANCELADO'])

class AgregarCarroInputSerializer(serializers.Serializer):
    servicio_id = serializers.IntegerField()
    asiento_id = serializers.IntegerField()
    nombre = serializers.CharField(max_length=150)
    rut = serializers.CharField(max_length=15)