"""
Script para poblar la base de datos con datos iniciales de prueba (AndesSur).
Ejecutar con: python poblar.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from transporte.models import PerfilUsuario, Ciudad, Bus, Asiento, Servicio

def ejecutar():
    print("Iniciando carga de datos de prueba...")

    # 1. USUARIOS Y PERFILES (ADMIN Y PASAJERO)
    admin_user, _ = User.objects.get_or_create(username='admin')
    admin_user.set_password('admin123')
    admin_user.is_staff = True
    admin_user.save()

    PerfilUsuario.objects.update_or_create(
        usuario=admin_user,
        defaults={'rol': 'ADMIN_FLOTA'}
    )

    pasajero_user, _ = User.objects.get_or_create(username='pasajero')
    pasajero_user.set_password('pasajero123')
    pasajero_user.is_staff = False
    pasajero_user.save()

    PerfilUsuario.objects.update_or_create(
        usuario=pasajero_user,
        defaults={'rol': 'PASAJERO'}
    )
    print("-> Usuarios 'admin' y 'pasajero' configurados.")

    # 2. CIUDADES
    ciudades_nombres = ['Santiago', 'Temuco', 'Concepción', 'Puerto Montt', 'Valdivia', 'La Serena']
    ciudades = {}
    for nombre in ciudades_nombres:
        c, _ = Ciudad.objects.get_or_create(nombre=nombre)
        ciudades[nombre] = c
    print("-> Ciudades registradas.")

    # 3. BUSES Y ASIENTOS
    buses_data = [
        ('ABCD12', 40),
        ('XYZW99', 40),
        ('ANDE44', 32),
    ]

    for patente, cap in buses_data:
        bus, creado = Bus.objects.get_or_create(patente=patente)
        if creado or bus.asientos.count() == 0:
            asientos = [
                Asiento(
                    bus=bus,
                    numero=n,
                    tipo='SALON_CAMA' if n <= 10 else 'SEMICAMA'
                )
                for n in range(1, cap + 1)
            ]
            Asiento.objects.bulk_create(asientos)
    print("-> Buses y asientos listos.")

    # 4. SERVICIOS / ITINERARIOS
    ahora = timezone.now()
    servicios_data = [
        (ciudades['Santiago'], ciudades['Temuco'], Bus.objects.get(patente='ABCD12'), ahora + timedelta(days=1, hours=2), 15000),
        (ciudades['Temuco'], ciudades['Santiago'], Bus.objects.get(patente='XYZW99'), ahora + timedelta(days=1, hours=8), 15000),
        (ciudades['Santiago'], ciudades['Concepción'], Bus.objects.get(patente='ANDE44'), ahora + timedelta(days=2, hours=4), 12000),
        (ciudades['Concepción'], ciudades['Puerto Montt'], Bus.objects.get(patente='ABCD12'), ahora + timedelta(days=3, hours=1), 18000),
    ]

    for origen, destino, bus, fecha, precio in servicios_data:
        Servicio.objects.get_or_create(
            origen=origen,
            destino=destino,
            bus=bus,
            fecha_salida=fecha,
            defaults={'precio_base': precio}
        )
    print("-> Servicios programados listos.")
    print("¡Base de datos poblada exitosamente!")

if __name__ == '__main__':
    ejecutar()