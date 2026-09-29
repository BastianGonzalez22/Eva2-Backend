"""
MÓDULO DE VISTAS WEB (transporte/views.py)
Contiene la lógica de catálogo, carro de compras persistente con reserva temporal
de asientos, checkout atómico y CRUD administrativo con validaciones estrictas.
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
# REGLAS DE NEGOCIO Y ACCESO
# ==============================================================================
def es_administrador(user):
    """Verifica si el usuario es staff o tiene perfil ADMIN_FLOTA."""
    return user.is_authenticated and (
        user.is_staff or 
        (hasattr(user, 'perfil') and user.perfil.rol == 'ADMIN_FLOTA')
    )

# ==============================================================================
# 1. CATÁLOGO Y BÚSQUEDA PÚBLICA
# ==============================================================================
def home(request):
    """Despliega el catálogo de itinerarios y filtra por origen y destino."""
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
# 2. AUTENTICACIÓN DE PASAJEROS
# ==============================================================================
def login_view(request):
    """Permite al pasajero autenticarse."""
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
    """Cierra la sesión activa."""
    auth_logout(request)
    return redirect('home')

def registro_view(request):
    """Crea una nueva cuenta de pasajero con su carro en PostgreSQL."""
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
# 3. DETALLE DE SERVICIO Y APARTADO DE ASIENTOS
# ==============================================================================
def detalle_servicio(request, servicio_id):
    """
    Mapa de asientos:
    Bloquea como ocupados tanto los boletos PAGADOS o ENTREGADOS,
    como los asientos apartados en carros activos de OTROS usuarios.
    """
    servicio = get_object_or_404(Servicio, id=servicio_id)
    asientos_query = servicio.bus.asientos.all().order_by('numero')
    
    # 1. Asientos vendidos definitivamente (PAGADO o ENTREGADO)
    ocupados_ventas = set(Boleto.objects.filter(
        servicio=servicio, 
        venta__estado__in=['PAGADO', 'ENTREGADO']
    ).values_list('asiento_id', flat=True))

    # 2. Asientos apartados en carros de otros usuarios (reserva en tránsito)
    query_carros = ItemCarro.objects.filter(servicio=servicio)
    if request.user.is_authenticated:
        # Excluir los apartados por el mismo usuario para que él sí los vea en su carro
        query_carros = query_carros.exclude(carro__usuario=request.user)
    ocupados_en_carros = set(query_carros.values_list('asiento_id', flat=True))

    # Unión de ambos bloqueos
    ocupados = list(ocupados_ventas.union(ocupados_en_carros))

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

        # Validación 1: ¿Ya está pagado o entregado?
        if Boleto.objects.filter(servicio=servicio, asiento=asiento_obj, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            messages.error(request, 'El asiento seleccionado ya fue comprado por otro usuario.')
            return redirect('detalle_servicio', servicio_id=servicio.id)

        # Validación 2: ¿Está en el carro de otro usuario en este instante?
        carro_ajeno = ItemCarro.objects.filter(servicio=servicio, asiento=asiento_obj).exclude(carro__usuario=request.user)
        if carro_ajeno.exists():
            messages.error(request, f'El asiento #{asiento_obj.numero} está actualmente reservado en el carro de otro pasajero.')
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
        messages.success(request, f'Asiento #{asiento_obj.numero} ({asiento_obj.get_tipo_display()}) agregado al carro.')
        return redirect('ver_carro')
        
    return render(request, 'servicio_detalle.html', {
        'servicio': servicio,
        'asientos': asientos,
        'ocupados': ocupados,
        'precio_semi': precio_semi,
        'precio_cama': precio_cama
    })

# ==============================================================================
# 4. GESTIÓN DEL CARRO Y CHECKOUT TRANSACCIONAL
# ==============================================================================
@login_required(login_url='login')
def ver_carro(request):
    """Muestra los pasajes apartados por el usuario."""
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
    """
    Quita un ítem del carro del usuario.
    Al eliminarlo, el asiento queda disponible de inmediato para todos.
    """
    carro = get_object_or_404(CarroPasajes, usuario=request.user)
    ItemCarro.objects.filter(carro=carro, id=item_id).delete()
    messages.info(request, 'Pasaje retirado del carro. El asiento vuelve a estar disponible.')
    return redirect('ver_carro')

@login_required(login_url='login')
@transaction.atomic
def procesar_checkout(request):
    """
    Checkout atómico: consolida los asientos apartados y emite los boletos.
    """
    carro = get_object_or_404(CarroPasajes, usuario=request.user)
    items = carro.items.select_for_update().select_related('servicio', 'asiento').all()
    
    if not items.exists():
        messages.error(request, 'Tu carro está vacío.')
        return redirect('home')

    for item in items:
        if Boleto.objects.filter(servicio=item.servicio, asiento=item.asiento, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            messages.error(request, f'El asiento #{item.asiento.numero} fue adquirido previamente.')
            return redirect('ver_carro')

    total = sum(item.obtener_subtotal() for item in items)
    venta = Venta.objects.create(
        usuario=request.user,
        total=total,
        estado='PAGADO'
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

    # Vaciar carro del usuario: libera cualquier bloqueo transitorio y consolida venta
    carro.items.all().delete()
    messages.success(request, '¡Compra realizada con éxito!')
    return redirect('comprobante', boleto_id=ultimo_boleto.id)

def comprobante(request, boleto_id):
    """Muestra el comprobante del boleto emitido."""
    boleto = get_object_or_404(Boleto, id=boleto_id)
    return render(request, 'comprobante.html', {'boleto': boleto})

# ==============================================================================
# 5. PANEL DE GESTIÓN (ADMIN_FLOTA - CRUD COMPLETO)
# ==============================================================================
def gestion_servicios(request):
    """Panel de administración para ciudades, buses y recorridos."""
    if not es_administrador(request.user):
        messages.error(request, 'Acceso denegado: Se requieren permisos de Administrador de Flota.')
        return redirect('home')

    ciudades = Ciudad.objects.all().order_by('nombre')
    buses = Bus.objects.all().order_by('patente')
    servicios = Servicio.objects.all().order_by('-fecha_salida')
    ahora_html = timezone.now().strftime('%Y-%m-%dT%H:%M')

    if request.method == 'POST':
        accion = request.POST.get('accion')

        # --- CIUDADES ---
        if accion == 'crear_ciudad':
            nombre_ciudad = request.POST.get('nombre_ciudad', '').strip()
            if nombre_ciudad:
                if Ciudad.objects.filter(nombre__iexact=nombre_ciudad).exists():
                    messages.error(request, f'La ciudad "{nombre_ciudad}" ya existe.')
                else:
                    Ciudad.objects.create(nombre=nombre_ciudad)
                    messages.success(request, f'Ciudad "{nombre_ciudad}" agregada.')
            return redirect('gestion_servicios')

        elif accion == 'editar_ciudad':
            ciudad_id = request.POST.get('ciudad_id')
            nuevo_nombre = request.POST.get('nombre_ciudad', '').strip()
            ciudad = get_object_or_404(Ciudad, id=ciudad_id)
            if nuevo_nombre:
                ciudad.nombre = nuevo_nombre
                ciudad.save()
                messages.success(request, f'Ciudad actualizada a "{nuevo_nombre}".')
            return redirect('gestion_servicios')

        elif accion == 'eliminar_ciudad':
            ciudad_id = request.POST.get('ciudad_id')
            ciudad = get_object_or_404(Ciudad, id=ciudad_id)
            if Servicio.objects.filter(models.Q(origen=ciudad) | models.Q(destino=ciudad)).exists():
                messages.error(request, f'No se puede eliminar "{ciudad.nombre}" porque tiene recorridos asignados.')
            else:
                ciudad.delete()
                messages.success(request, f'Ciudad "{ciudad.nombre}" eliminada.')
            return redirect('gestion_servicios')

        # --- BUSES ---
        elif accion == 'crear_bus':
            patente = request.POST.get('patente', '').strip().upper()
            try:
                cantidad = int(request.POST.get('capacidad', 40))
            except ValueError:
                cantidad = 40

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
                    for n in range(1, cantidad + 1)
                ]
                Asiento.objects.bulk_create(asientos)
                messages.success(request, f'Bus {patente} registrado con {cantidad} asientos.')
            return redirect('gestion_servicios')

        elif accion == 'editar_bus':
            bus_id = request.POST.get('bus_id')
            nueva_patente = request.POST.get('patente', '').strip().upper()
            bus = get_object_or_404(Bus, id=bus_id)
            if nueva_patente:
                if Bus.objects.filter(patente=nueva_patente).exclude(id=bus.id).exists():
                    messages.error(request, f'Ya existe otro bus con la patente {nueva_patente}.')
                else:
                    bus.patente = nueva_patente
                    bus.save()
                    messages.success(request, f'Patente actualizada a "{nueva_patente}".')
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

        # --- RECORRIDOS ---
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
                    messages.error(request, 'No puedes reprogramar un recorrido en una fecha u hora pasada.')
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
            messages.success(request, f'Servicio #{servicio.id} modificado exitosamente.')
            return redirect('gestion_servicios')

        elif accion == 'eliminar_servicio':
            servicio_id = request.POST.get('servicio_id')
            servicio = get_object_or_404(Servicio, id=servicio_id)

            # Valida contra PAGADO y ENTREGADO
            if Boleto.objects.filter(servicio=servicio, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
                messages.error(request, 'No se puede eliminar: tiene boletos pagados o entregados.')
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