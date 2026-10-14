from django.contrib import admin
from .models import (
    PerfilUsuario, TipoAsiento, Ciudad, Bus, 
    Asiento, Servicio, CarroPasajes, ItemCarro, Venta, Boleto
)

@admin.register(TipoAsiento)
class TipoAsientoAdmin(admin.ModelAdmin):
    list_display = ('id', 'codigo', 'nombre', 'multiplicador_precio')
    search_fields = ('codigo', 'nombre')

@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = (
        'usuario', 'rol', 'rut', 'telefono', 
        'fecha_nacimiento', 'contacto_emergencia_nombre', 'contacto_emergencia_telefono'
    )
    list_filter = ('rol',)
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name', 'rut')

@admin.register(Boleto)
class BoletoAdmin(admin.ModelAdmin):
    list_display = ('id', 'venta', 'servicio', 'asiento', 'nombre_pasajero', 'apellido_pasajero', 'rut_pasajero', 'es_menor', 'precio_pagado')
    list_filter = ('es_menor', 'venta__estado')
    search_fields = ('rut_pasajero', 'nombre_pasajero', 'apellido_pasajero')

admin.site.register(Ciudad)
admin.site.register(Bus)
admin.site.register(Asiento)
admin.site.register(Servicio)
admin.site.register(CarroPasajes)
admin.site.register(ItemCarro)
admin.site.register(Venta)