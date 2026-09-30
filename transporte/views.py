"""
MÓDULO DE VISTAS WEB (transporte/views.py)
Catálogo, autenticación, carro de compras, flujo PENDIENTE -> PAGADO,
comprobante y panel administrativo para gestión de flota.
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction, models

from .models import (
    Servicio, Asiento, Boleto, Ciudad, Bus, Venta, 
    CarroPasajes, ItemCarro, validar_rut_chileno
)

# ==============================================================================
# PERMISOS
# ==============================================================================
def es_administrador(user):
    return user.is_authenticated and (
        user.is_staff or 
        (hasattr(user, 'perfil') and user.perfil.rol == 'ADMIN_FLOTA')
    )

# ==============================================================================
# 1. CATÁLOGO Y BÚSQUEDA PÚBLICA
# ==============================================================================
def home(request):
    origen_id = request.GET.get('origen')
    destino_id = request.GET.get('destino')
    
    servicios = Servicio.objects.filter(fecha_salida__gte=timezone.now()).order_by('fecha_salida')
    if origen_id:
        servicios = servicios.filter(origen_id=origen_id)
    if destino_id:
        servicios = servicios.filter(destino_id=destino_id)
        
    ciudades = Ciudad.objects.all().order_by('nombre')
    return render(request, 'home.html', {
        'servicios': servicios,
        'ciudades': ciudades,
        'origen_sel': origen_id,
        'destino_sel': destino_id
    })

# ==============================================================================
# 2. AUTENTICACIÓN
# ==============================================================================
def login_view(request):
    if request.method == 'POST':
        user_input = request.POST.get('username')
        pass_input = request.POST.get('password')
        usuario = authenticate(request, username=user_input, password=pass_input)
        if usuario:
            auth_login(request, usuario)
            return redirect('home')
        messages.error(request, 'Credenciales inválidas. Verifica tu usuario y contraseña.')
    return render(request, 'login.html')

def logout_view(request):
    auth_logout(request)
    return redirect('home')

def registro_view(request):
    if request.method == 'POST':
        user_input = request.POST.get('username')
        pass_input = request.POST.get('password')
        if User.objects.filter(username=user_input).exists():
            messages.error(request, 'El nombre de usuario ya está registrado.')
        else:
            user = User.objects.create_user(username=user_input, password=pass_input)
            CarroPasajes.objects.create(usuario=user)
            auth_login(request, user)
            messages.success(request, 'Cuenta creada exitosamente.')
            return redirect('home')
    return render(request, 'registro.html')

# ==============================================================================
# 3. DETALLE DE SERVICIO Y MAPA DE ASIENTOS
# ==============================================================================
def detalle_servicio(request, servicio_id):
    servicio = get_object_or_404(Servicio, id=servicio_id)
    asientos_query = servicio.bus.asientos.all().order_by('numero')
    
    ocupados = list(Boleto.objects.filter(
        servicio=servicio, 
        venta__estado__in=['PAGADO', 'ENTREGADO']
    ).values_list('asiento_id', flat=True))

    asientos = []
    for a in asientos_query:
        a.precio_calculado = a.calcular_precio(servicio.precio_base)
        asientos.append(a)

    precio_semi = servicio.precio_base
    precio_cama = int(servicio.precio_base * 1.20)
    
    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.info(request, 'Debes iniciar sesión para agregar pasajes al carro.')
            return redirect('login')
            
        asiento_id = request.POST.get('asiento_id')
        rut = request.POST.get('rut', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        
        if not asiento_id or not rut or not nombre:
            messages.error(request, 'Todos los campos son obligatorios.')
            return redirect('detalle_servicio', servicio_id=servicio.id)
            
        try:
            validar_rut_chileno(rut)
        except ValidationError as e:
            messages.error(request, str(e.message))
            return redirect('detalle_servicio', servicio_id=servicio.id)
            
        asiento_obj = get_object_or_404(Asiento, id=asiento_id, bus=servicio.bus)

        if Boleto.objects.filter(servicio=servicio, asiento=asiento_obj, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            messages.error(request, 'El asiento seleccionado ya fue adquirido.')
            return redirect('detalle_servicio', servicio_id=servicio.id)

        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        
        if ItemCarro.objects.filter(carro=carro, servicio=servicio, asiento=asiento_obj).exists():
            messages.warning(request, 'Este pasaje ya está en tu carro de compras.')
            return redirect('ver_carro')
            
        ItemCarro.objects.create(
            carro=carro,
            servicio=servicio,
            asiento=asiento_obj,
            nombre_ocupante=nombre,
            rut_ocupante=rut
        )
        messages.success(request, f'Asiento #{asiento_obj.numero} agregado al carro.')
        return redirect('ver_carro')
        
    return render(request, 'servicio_detalle.html', {
        'servicio': servicio,
        'asientos': asientos,
        'ocupados': ocupados,
        'precio_semi': precio_semi,
        'precio_cama': precio_cama
    })

# ==============================================================================
# 4. CARRO DE COMPRAS, CHECKOUT (PENDIENTE) Y PAGO CON SELECT_FOR_UPDATE
# ==============================================================================
@login_required(login_url='login')
def ver_carro(request):
    carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
    items = carro.items.select_related('servicio', 'asiento').all()
    
    items_detalle = []
    total = 0
    for i in items:
        precio_item = i.obtener_subtotal()
        total += precio_item
        items_detalle.append({
            'item': i,
            'precio': precio_item
        })

    return render(request, 'carro.html', {
        'carro': carro, 
        'items_detalle': items_detalle, 
        'total': total
    })

@login_required(login_url='login')
def eliminar_item_carro(request, item_id):
    carro = get_object_or_404(CarroPasajes, usuario=request.user)
    ItemCarro.objects.filter(carro=carro, id=item_id).delete()
    messages.info(request, 'Pasaje retirado del carro.')
    return redirect('ver_carro')

@login_required(login_url='login')
@transaction.atomic
def procesar_checkout(request):
    """Crea la venta en estado PENDIENTE y vacía el carro."""
    carro = get_object_or_404(CarroPasajes, usuario=request.user)
    items = list(carro.items.select_related('servicio', 'asiento').all())
    
    if not items:
        messages.error(request, 'Tu carro está vacío.')
        return redirect('home')

    total = sum(item.obtener_subtotal() for item in items)
    venta = Venta.objects.create(
        usuario=request.user,
        total=total,
        estado='PENDIENTE'
    )

    ultimo_boleto = None
    for item in items:
        ultimo_boleto = Boleto.objects.create(
            venta=venta,
            servicio=item.servicio,
            asiento=item.asiento,
            nombre_pasajero=item.nombre_ocupante,
            rut_pasajero=item.rut_ocupante,
            precio_pagado=item.obtener_subtotal()
        )

    carro.items.all().delete()
    messages.info(request, 'Orden generada en estado PENDIENTE. Confirma tu pago a continuación.')
    return redirect('comprobante', boleto_id=ultimo_boleto.id)

@login_required(login_url='login')
@transaction.atomic
def pagar_orden_web(request, venta_id):
    """Pasa la orden de PENDIENTE a PAGADO con bloqueo pesimista en Asiento."""
    venta = get_object_or_404(Venta, id=venta_id, usuario=request.user)

    if venta.estado != 'PENDIENTE':
        messages.warning(request, f'La orden ya se encuentra en estado {venta.estado}.')
        primer_boleto = venta.boletos.first()
        return redirect('comprobante', boleto_id=primer_boleto.id)

    boletos = list(venta.boletos.select_related('servicio', 'asiento').all())
    asientos_ids = [b.asiento_id for b in boletos]

    # Bloqueo pesimista sobre las butacas
    list(Asiento.objects.filter(id__in=asientos_ids).select_for_update())

    for b in boletos:
        if Boleto.objects.filter(
            servicio=b.servicio, 
            asiento=b.asiento, 
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).exclude(venta=venta).exists():
            messages.error(request, f'El asiento #{b.asiento.numero} ya fue adquirido por otro usuario.')
            return redirect('comprobante', boleto_id=b.id)

    venta.estado = 'PAGADO'
    venta.save()
    messages.success(request, '¡Pago realizado con éxito! Tus pasajes han sido emitidos.')
    return redirect('comprobante', boleto_id=boletos[0].id)

@login_required(login_url='login')
def cancelar_orden_web(request, venta_id):
    """Cancela la orden y libera los asientos."""
    venta = get_object_or_404(Venta, id=venta_id, usuario=request.user)
    if venta.estado in ['PENDIENTE', 'PAGADO']:
        venta.estado = 'CANCELADO'
        venta.save()
        messages.success(request, 'Orden cancelada exitosamente.')
    else:
        messages.error(request, 'No se puede cancelar esta orden.')
    primer_boleto = venta.boletos.first()
    return redirect('comprobante', boleto_id=primer_boleto.id)

def comprobante(request, boleto_id):
    """Muestra el comprobante con estado y opciones de pago."""
    boleto = get_object_or_404(Boleto, id=boleto_id)
    return render(request, 'comprobante.html', {
        'boleto': boleto,
        'venta': boleto.venta
    })

# ==============================================================================
# 5. GESTIÓN DE FLOTA (ADMINISTRADOR)
# ==============================================================================
def gestion_servicios(request):
    if not es_administrador(request.user):
        messages.error(request, 'Acceso denegado: Requiere rol ADMIN_FLOTA.')
        return redirect('home')

    ciudades = Ciudad.objects.all().order_by('nombre')
    buses = Bus.objects.all().order_by('patente')
    servicios = Servicio.objects.all().order_by('-fecha_salida')
    ahora_html = timezone.now().strftime('%Y-%m-%dT%H:%M')

    if request.method == 'POST':
        accion = request.POST.get('accion')

        if accion == 'crear_ciudad':
            nombre = request.POST.get('nombre_ciudad', '').strip()
            if nombre:
                if Ciudad.objects.filter(nombre__iexact=nombre).exists():
                    messages.error(request, f'La ciudad "{nombre}" ya existe.')
                else:
                    Ciudad.objects.create(nombre=nombre)
                    messages.success(request, f'Ciudad "{nombre}" creada.')
            return redirect('gestion_servicios')

        elif accion == 'editar_ciudad':
            c_id = request.POST.get('ciudad_id')
            nombre = request.POST.get('nombre_ciudad', '').strip()
            ciudad = get_object_or_404(Ciudad, id=c_id)
            if nombre:
                ciudad.nombre = nombre
                ciudad.save()
                messages.success(request, f'Ciudad actualizada a "{nombre}".')
            return redirect('gestion_servicios')

        elif accion == 'eliminar_ciudad':
            c_id = request.POST.get('ciudad_id')
            ciudad = get_object_or_404(Ciudad, id=c_id)
            if Servicio.objects.filter(models.Q(origen=ciudad) | models.Q(destino=ciudad)).exists():
                messages.error(request, f'No se puede eliminar "{ciudad.nombre}" porque tiene servicios asignados.')
            else:
                ciudad.delete()
                messages.success(request, f'Ciudad "{ciudad.nombre}" eliminada.')
            return redirect('gestion_servicios')

        elif accion == 'crear_bus':
            patente = request.POST.get('patente', '').strip().upper()
            try:
                capacidad = int(request.POST.get('capacidad', 40))
            except ValueError:
                capacidad = 40

            if Bus.objects.filter(patente=patente).exists():
                messages.error(request, f'Ya existe un bus con la patente {patente}.')
            else:
                bus = Bus.objects.create(patente=patente)
                asientos = [
                    Asiento(
                        bus=bus, 
                        numero=n, 
                        tipo='SALON_CAMA' if n <= 10 else 'SEMICAMA'
                    )
                    for n in range(1, capacidad + 1)
                ]
                Asiento.objects.bulk_create(asientos)
                messages.success(request, f'Bus {patente} registrado con {capacidad} asientos.')
            return redirect('gestion_servicios')

        elif accion == 'editar_bus':
            bus_id = request.POST.get('bus_id')
            patente = request.POST.get('patente', '').strip().upper()
            bus = get_object_or_404(Bus, id=bus_id)
            if patente:
                if Bus.objects.filter(patente=patente).exclude(id=bus.id).exists():
                    messages.error(request, f'Ya existe otro bus con la patente {patente}.')
                else:
                    bus.patente = patente
                    bus.save()
                    messages.success(request, f'Patente actualizada a "{patente}".')
            return redirect('gestion_servicios')

        elif accion == 'eliminar_bus':
            bus_id = request.POST.get('bus_id')
            bus = get_object_or_404(Bus, id=bus_id)
            if Servicio.objects.filter(bus=bus).exists():
                messages.error(request, f'No se puede eliminar el bus {bus.patente} porque tiene recorridos asociados.')
            else:
                bus.asientos.all().delete()
                bus.delete()
                messages.success(request, f'Bus {bus.patente} eliminado.')
            return redirect('gestion_servicios')

        elif accion == 'crear_servicio':
            origen_id = request.POST.get('origen')
            destino_id = request.POST.get('destino')
            bus_id = request.POST.get('bus')
            fecha_str = request.POST.get('fecha_salida')
            precio = request.POST.get('precio_base')

            if origen_id == destino_id:
                messages.error(request, 'El origen y destino no pueden ser iguales.')
                return redirect('gestion_servicios')

            try:
                fecha_salida_dt = timezone.datetime.fromisoformat(fecha_str)
                if timezone.is_naive(fecha_salida_dt):
                    fecha_salida_dt = timezone.make_aware(fecha_salida_dt)
                if fecha_salida_dt < timezone.now():
                    messages.error(request, 'No puedes programar en fechas u horas pasadas.')
                    return redirect('gestion_servicios')
            except (ValueError, TypeError):
                messages.error(request, 'Formato de fecha inválido.')
                return redirect('gestion_servicios')

            Servicio.objects.create(
                origen_id=origen_id,
                destino_id=destino_id,
                bus_id=bus_id,
                fecha_salida=fecha_salida_dt,
                precio_base=precio
            )
            messages.success(request, 'Servicio publicado correctamente.')
            return redirect('gestion_servicios')

        elif accion == 'editar_servicio':
            servicio_id = request.POST.get('servicio_id')
            servicio = get_object_or_404(Servicio, id=servicio_id)

            origen_id = request.POST.get('origen')
            destino_id = request.POST.get('destino')
            bus_id = request.POST.get('bus')
            fecha_str = request.POST.get('fecha_salida')
            precio = request.POST.get('precio_base')

            if origen_id == destino_id:
                messages.error(request, 'El origen y destino no pueden ser iguales.')
                return redirect('gestion_servicios')

            try:
                fecha_salida_dt = timezone.datetime.fromisoformat(fecha_str)
                if timezone.is_naive(fecha_salida_dt):
                    fecha_salida_dt = timezone.make_aware(fecha_salida_dt)
                if fecha_salida_dt < timezone.now():
                    messages.error(request, 'No puedes reprogramar un recorrido en fecha pasada.')
                    return redirect('gestion_servicios')
            except (ValueError, TypeError):
                messages.error(request, 'Formato de fecha inválido.')
                return redirect('gestion_servicios')

            servicio.origen_id = origen_id
            servicio.destino_id = destino_id
            servicio.bus_id = bus_id
            servicio.fecha_salida = fecha_salida_dt
            servicio.precio_base = precio
            servicio.save()
            messages.success(request, f'Servicio #{servicio.id} actualizado.')
            return redirect('gestion_servicios')

        elif accion == 'eliminar_servicio':
            servicio_id = request.POST.get('servicio_id')
            servicio = get_object_or_404(Servicio, id=servicio_id)

            if Boleto.objects.filter(servicio=servicio, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
                messages.error(request, 'No se puede eliminar: tiene boletos asociados.')
            else:
                ItemCarro.objects.filter(servicio=servicio).delete()
                servicio.delete()
                messages.success(request, f'Servicio #{servicio_id} eliminado.')
            return redirect('gestion_servicios')

    for s in servicios:
        s.fecha_salida_html = s.fecha_salida.strftime('%Y-%m-%dT%H:%M')

    return render(request, 'gestion.html', {
        'ciudades': ciudades,
        'buses': buses,
        'servicios': servicios,
        'ahora_html': ahora_html
    })

def error_404_view(request, exception=None):
    return render(request, '404.html', status=404)