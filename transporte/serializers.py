from rest_framework import serializers
from .models import (
    TipoAsiento, PerfilUsuario, Ciudad, Bus, Asiento, 
    Servicio, Boleto, Venta, CarroPasajes, ItemCarro
)


class TipoAsientoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoAsiento
        fields = ['id', 'codigo', 'nombre', 'multiplicador_precio']


class PerfilUsuarioSerializer(serializers.ModelSerializer):
    username = serializers.ReadOnlyField(source='usuario.username')
    nombres = serializers.CharField(source='usuario.first_name', required=False)
    apellidos = serializers.CharField(source='usuario.last_name', required=False)
    email = serializers.EmailField(source='usuario.email', required=False)

    class Meta:
        model = PerfilUsuario
        fields = [
            'id', 'usuario', 'username', 'nombres', 'apellidos', 'email',
            'rol', 'rut', 'telefono', 'fecha_nacimiento',
            'contacto_emergencia_nombre', 'contacto_emergencia_telefono'
        ]

    def update(self, instance, validated_data):
        user_data = validated_data.pop('usuario', {})
        user = instance.usuario
        if 'first_name' in user_data:
            user.first_name = user_data['first_name']
        if 'last_name' in user_data:
            user.last_name = user_data['last_name']
        if 'email' in user_data:
            user.email = user_data['email']
        user.save()
        return super().update(instance, validated_data)


class CiudadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ciudad
        fields = ['id', 'nombre']


class BusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bus
        fields = ['id', 'patente']


class AsientoSerializer(serializers.ModelSerializer):
    tipo = serializers.ReadOnlyField()
    tipo_nombre = serializers.ReadOnlyField(source='get_tipo_display')
    precio = serializers.IntegerField(read_only=True)
    ocupado = serializers.BooleanField(read_only=True)

    class Meta:
        model = Asiento
        fields = ['id', 'numero', 'tipo_asiento', 'tipo', 'tipo_nombre', 'precio', 'ocupado']


class ServicioSerializer(serializers.ModelSerializer):
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
    asiento_numero = serializers.ReadOnlyField(source='asiento.numero')
    servicio_desc = serializers.StringRelatedField(source='servicio')

    class Meta:
        model = Boleto
        fields = [
            'id', 'venta', 'servicio', 'servicio_desc', 
            'asiento', 'asiento_numero', 'nombre_pasajero', 
            'apellido_pasajero', 'rut_pasajero', 'es_menor', 'precio_pagado'
        ]


class VentaSerializer(serializers.ModelSerializer):
    boletos = BoletoSerializer(many=True, read_only=True)
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')

    class Meta:
        model = Venta
        fields = ['id', 'usuario', 'usuario_nombre', 'fecha_venta', 'total', 'estado', 'boletos']


class ItemCarroSerializer(serializers.ModelSerializer):
    subtotal = serializers.SerializerMethodField()
    asiento_numero = serializers.ReadOnlyField(source='asiento.numero')
    servicio_desc = serializers.StringRelatedField(source='servicio')

    class Meta:
        model = ItemCarro
        fields = [
            'id', 'servicio', 'servicio_desc', 'asiento', 
            'asiento_numero', 'nombre_ocupante', 'apellido_ocupante',
            'rut_ocupante', 'es_menor', 'subtotal'
        ]

    def get_subtotal(self, obj):
        return obj.obtener_subtotal()


class CarroPasajesSerializer(serializers.ModelSerializer):
    items = ItemCarroSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = CarroPasajes
        fields = ['id', 'usuario', 'items', 'total']

    def get_total(self, obj):
        return sum(item.obtener_subtotal() for item in obj.items.all())


class PasajeroIndividualInputSerializer(serializers.Serializer):
    asiento_id = serializers.IntegerField(help_text="ID de la butaca")
    nombre = serializers.CharField(max_length=100, help_text="Nombre del pasajero")
    apellido = serializers.CharField(max_length=100, help_text="Apellido del pasajero")
    rut = serializers.CharField(max_length=12, help_text="RUT chileno con guion")
    es_menor = serializers.BooleanField(default=False, required=False, help_text="¿Es menor de edad?")


class AgregarCarroInputSerializer(serializers.Serializer):
    servicio_id = serializers.IntegerField(help_text="ID del servicio programado")
    pasajeros = PasajeroIndividualInputSerializer(many=True, required=False)
    asiento_id = serializers.IntegerField(required=False)
    nombre = serializers.CharField(max_length=100, required=False)
    apellido = serializers.CharField(max_length=100, required=False, default='')
    rut = serializers.CharField(max_length=12, required=False)
    es_menor = serializers.BooleanField(default=False, required=False)


class CambiarEstadoSerializer(serializers.Serializer):
    estado = serializers.ChoiceField(
        choices=['PENDIENTE', 'PAGADO', 'ENTREGADO', 'CANCELADO'],
        help_text="Nuevo estado a asignar a la orden de venta"
    )