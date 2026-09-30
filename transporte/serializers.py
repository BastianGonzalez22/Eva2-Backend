"""
==============================================================================
MÓDULO DE SERIALIZADORES DRF (transporte/serializers.py)
------------------------------------------------------------------------------
Transforma las estructuras de datos entre instancias de modelos ORM y 
representaciones JSON para la API REST. Incluye serializadores de lectura, 
escritura con validación, desgloses calculados y esquemas para Swagger/OpenAPI.
==============================================================================
"""

from rest_framework import serializers
from .models import Ciudad, Bus, Asiento, Servicio, Boleto, Venta, CarroPasajes, ItemCarro


class CiudadSerializer(serializers.ModelSerializer):
    """
    Serializador para la entidad Ciudad. Expone id y nombre para listados
    y selección de orígenes/destinos.
    """
    class Meta:
        model = Ciudad
        fields = ['id', 'nombre']


class BusSerializer(serializers.ModelSerializer):
    """
    Serializador para la entidad Bus. Expone id y la patente vehicular única.
    """
    class Meta:
        model = Bus
        fields = ['id', 'patente']


class AsientoSerializer(serializers.ModelSerializer):
    """
    Serializador para inspección de butacas. Informa número, tipo de asiento,
    precio dinámico calculado y disponibilidad booleana en tiempo real.
    """
    precio = serializers.IntegerField(read_only=True)
    ocupado = serializers.BooleanField(read_only=True)

    class Meta:
        model = Asiento
        fields = ['id', 'numero', 'tipo', 'precio', 'ocupado']


class ServicioSerializer(serializers.ModelSerializer):
    """
    Serializador para servicios de transporte. Soporta lectura anidada con
    nombres de ciudades y patente de bus, así como creación/edición mediante IDs.
    """
    origen_nombre = serializers.ReadOnlyField(source='origen.nombre')
    destino_nombre = serializers.ReadOnlyField(source='destino.nombre')
    bus_patente = serializers.ReadOnlyField(source='bus.patente')

    class Meta:
        model = Servicio
        fields = [
            'id', 'origen', 'destino', 'bus', 
            'origen_nombre', 'destino_nombre', 'bus_patente',
            'fecha_salida', 'precio_base'
        ]


class BoletoSerializer(serializers.ModelSerializer):
    """
    Serializador para emisión y consulta de boletos de pasaje individuales.
    Desglosa datos de ruta, butaca, pasajero y precio efectivamente cobrado.
    """
    asiento_numero = serializers.ReadOnlyField(source='asiento.numero')
    servicio_desc = serializers.StringRelatedField(source='servicio')

    class Meta:
        model = Boleto
        fields = [
            'id', 'venta', 'servicio', 'servicio_desc', 
            'asiento', 'asiento_numero', 'nombre_pasajero', 
            'rut_pasajero', 'precio_pagado'
        ]


class VentaSerializer(serializers.ModelSerializer):
    """
    Serializador de cabecera de orden de compra. Serializa el cliente, total,
    fecha, estado transaccional y la lista de boletos asociados emitidos.
    """
    boletos = BoletoSerializer(many=True, read_only=True)
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')

    class Meta:
        model = Venta
        fields = ['id', 'usuario', 'usuario_nombre', 'fecha_venta', 'total', 'estado', 'boletos']


class ItemCarroSerializer(serializers.ModelSerializer):
    """
    Serializador para cada butaca reservada dentro del carro de compras.
    Calcula el subtotal unitario según la categoría del asiento.
    """
    subtotal = serializers.SerializerMethodField()
    asiento_numero = serializers.ReadOnlyField(source='asiento.numero')
    servicio_desc = serializers.StringRelatedField(source='servicio')

    class Meta:
        model = ItemCarro
        fields = [
            'id', 'servicio', 'servicio_desc', 'asiento', 
            'asiento_numero', 'nombre_ocupante', 'rut_ocupante', 'subtotal'
        ]

    def get_subtotal(self, obj):
        """
        Retorna el subtotal monetario del ítem invocando la lógica del modelo.
        """
        return obj.obtener_subtotal()


class CarroPasajesSerializer(serializers.ModelSerializer):
    """
    Serializador del carro de compras persistente. Expone el desglose de ítems
    y computa la sumatoria acumulada total de la orden.
    """
    items = ItemCarroSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = CarroPasajes
        fields = ['id', 'usuario', 'items', 'total']

    def get_total(self, obj):
        """
        Calcula la sumatoria de todos los subtotales de los pasajes en el carro.
        """
        return sum(item.obtener_subtotal() for item in obj.items.all())


class AgregarCarroInputSerializer(serializers.Serializer):
    """
    Serializador de validación para la acción POST de agregar un pasaje al carro.
    Requiere servicio_id, asiento_id, nombre del ocupante y RUT chileno.
    """
    servicio_id = serializers.IntegerField(help_text="ID del servicio programado")
    asiento_id = serializers.IntegerField(help_text="ID de la butaca a seleccionar")
    nombre = serializers.CharField(max_length=150, help_text="Nombre completo del pasajero")
    rut = serializers.CharField(max_length=12, help_text="RUT chileno del pasajero (con guion)")


class CambiarEstadoSerializer(serializers.Serializer):
    """
    Serializador para la transición administrativa de estados de una Venta.
    Restringe la entrada a los estados válidos del ciclo de vida del negocio.
    """
    estado = serializers.ChoiceField(
        choices=['PENDIENTE', 'PAGADO', 'ENTREGADO', 'CANCELADO'],
        help_text="Nuevo estado a asignar a la orden de venta"
    )