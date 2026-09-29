"""
SCRIPT DE POBLADO DE DATOS (poblar.py)
Carga datos iniciales de prueba para evaluación docente:
Usuarios (Admin/Pasajero), Ciudades, Buses con distribución de asientos y Recorridos.
"""

import os
import django

# Apunta al módulo correcto core.settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from transporte.models import Ciudad, Bus, Asiento, Servicio, PerfilUsuario

def poblar():
    print("Iniciando carga de datos de prueba...")

    # 1. Crear Administrador y Pasajero
    admin_user, _ = User.objects.get_or_create(username='admin', defaults={'is_staff': True})
    admin_user.set_password('admin123')
    admin_user.is_staff = True
    admin_user.save()
    PerfilUsuario.objects.update_or_create(usuario=admin_user, defaults={'rol': 'ADMIN_FLOTA'})

    pasajero, _ = User.objects.get_or_create(username='pasajero')
    pasajero.set_password('pasajero123')
    pasajero.save()
    PerfilUsuario.objects.update_or_create(usuario=pasajero, defaults={'rol': 'PASAJERO'})

    # 2. Ciudades
    ciudades_nombres = ['Santiago', 'Concepción', 'Temuco', 'Chillán']
    ciudades_objs = {}
    for nombre in ciudades_nombres:
        c, _ = Ciudad.objects.get_or_create(nombre=nombre)
        ciudades_objs[nombre] = c

    # 3. Buses con asientos (10 Salón Cama y 30 Semicama)
    buses_data = ['ABCD-12', 'KJHG-44', 'TRPL-88']
    buses_objs = []
    for pat in buses_data:
        bus, created = Bus.objects.get_or_create(patente=pat)
        if created or bus.asientos.count() == 0:
            asientos = [
                Asiento(bus=bus, numero=n, tipo='SALON_CAMA' if n <= 10 else 'SEMICAMA')
                for n in range(1, 41)
            ]
            Asiento.objects.bulk_create(asientos)
        buses_objs.append(bus)

    # 4. Servicios futuros
    ahora = timezone.now()
    Servicio.objects.get_or_create(
        origen=ciudades_objs['Santiago'],
        destino=ciudades_objs['Temuco'],
        bus=buses_objs[0],
        fecha_salida=ahora + timedelta(days=1, hours=4),
        defaults={'precio_base': 15000}
    )
    Servicio.objects.get_or_create(
        origen=ciudades_objs['Concepción'],
        destino=ciudades_objs['Santiago'],
        bus=buses_objs[1],
        fecha_salida=ahora + timedelta(days=2, hours=2),
        defaults={'precio_base': 12000}
    )

    print("¡Base de datos poblada exitosamente!")
    print("Credenciales de prueba:")
    print("  -> Administrador: admin / admin123")
    print("  -> Pasajero:      pasajero / pasajero123")

if __name__ == '__main__':
    poblar()