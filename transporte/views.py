import random
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction, models

from transbank.webpay.webpay_plus.transaction import Transaction
from transbank.common.options import WebpayOptions
from transbank.common.integration_commerce_codes import IntegrationCommerceCodes
from transbank.common.integration_api_keys import IntegrationApiKeys
from transbank.common.integration_type import IntegrationType

from .models import (
    TipoAsiento, Servicio, Asiento, Boleto, Ciudad, Bus, Venta, 
    CarroPasajes, ItemCarro, validar_rut_chileno
)

# ==============================================================================
# SECCIÓN 1: CONTROL DE PERMISOS Y AUTORIZACIÓN (RBAC)
# ==============================================================================
def es_administrador(user):
    return user.is_authenticated and (
        user.is_staff or 
        (hasattr(user, 'perfil') and user.perfil.rol == 'ADMIN_FLOTA')
    )

# ==============================================================================
# SECCIÓN 2: CATÁLOGO Y BÚSQUEDA PÚBLICA DE RECORRIDOS
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
# SECCIÓN 3: AUTENTICACIÓN Y GESTIÓN DE PASAJEROS ENRIQUECIDA
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
        user_input = request.POST.get('username', '').strip()
        pass_input = request.POST.get('password', '')
        nombres_input = request.POST.get('first_name', '').strip()
        apellidos_input = request.POST.get('last_name', '').strip()
        email_input = request.POST.get('email', '').strip()
        rut_input = request.POST.get('rut', '').strip()
        telefono_input = request.POST.get('telefono', '').strip()
        fecha_nac_input = request.POST.get('fecha_nacimiento', '').strip()
        emergencia_nombre = request.POST.get('contacto_emergencia_nombre', '').strip()
        emergencia_fono = request.POST.get('contacto_emergencia_telefono', '').strip()

        if User.objects.filter(username=user_input).exists():
            messages.error(request, 'El nombre de usuario ya está registrado.')
            return render(request, 'registro.html')

        if rut_input:
            try:
                validar_rut_chileno(rut_input)
            except ValidationError as e:
                messages.error(request, str(e.message))
                return render(request, 'registro.html')

        user = User.objects.create_user(
            username=user_input,
            password=pass_input,
            first_name=nombres_input,
            last_name=apellidos_input,
            email=email_input
        )
        perfil = user.perfil
        perfil.rut = rut_input or None
        perfil.telefono = telefono_input or None
        if fecha_nac_input:
            perfil.fecha_nacimiento = fecha_nac_input
        perfil.contacto_emergencia_nombre = emergencia_nombre or None
        perfil.contacto_emergencia_telefono = emergencia_fono or None
        perfil.save()

        CarroPasajes.objects.create(usuario=user)
        auth_login(request, user)
        messages.success(request, 'Ficha de pasajero y cuenta creadas exitosamente.')
        return redirect('home')
    return render(request, 'registro.html')

# ==============================================================================
# SECCIÓN 4: DETALLE DE SERVICIO, MAPA DE ASIENTOS Y COMPRA MÚLTIPLE
# ==============================================================================
def detalle_servicio(request, servicio_id):
    servicio = get_object_or_404(Servicio, id=servicio_id)
    asientos_query = servicio.bus.asientos.select_related('tipo_asiento').all().order_by('numero')
    
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
            
        asientos_ids = request.POST.getlist('asientos_ids')
        if not asientos_ids and request.POST.get('asiento_id'):
            asientos_ids = [request.POST.get('asiento_id')]

        if not asientos_ids:
            messages.error(request, 'Debes seleccionar al menos un asiento del mapa.')
            return redirect('detalle_servicio', servicio_id=servicio.id)

        pasajeros_validados = []
        ruts_en_seleccion = set()

        for a_id in asientos_ids:
            nombre = request.POST.get(f'nombre_{a_id}', request.POST.get('nombre', '')).strip()
            apellido = request.POST.get(f'apellido_{a_id}', request.POST.get('apellido', '')).strip()
            rut = request.POST.get(f'rut_{a_id}', request.POST.get('rut', '')).strip()
            es_menor = request.POST.get(f'es_menor_{a_id}') == 'on'

            if not nombre or not apellido or not rut:
                messages.error(request, 'Todos los campos (Nombre, Apellido y RUT) son obligatorios para cada pasajero.')
                return redirect('detalle_servicio', servicio_id=servicio.id)

            try:
                validar_rut_chileno(rut)
            except ValidationError as e:
                messages.error(request, f'RUT ({rut}): {e.message}')
                return redirect('detalle_servicio', servicio_id=servicio.id)

            rut_normalizado = rut.upper().replace('.', '')
            if rut_normalizado in ruts_en_seleccion:
                messages.error(request, f'No puedes repetir el mismo RUT ({rut}) en dos asientos distintos del mismo viaje.')
                return redirect('detalle_servicio', servicio_id=servicio.id)
            ruts_en_seleccion.add(rut_normalizado)

            asiento_obj = get_object_or_404(Asiento, id=a_id, bus=servicio.bus)
            if Boleto.objects.filter(servicio=servicio, asiento=asiento_obj, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
                messages.error(request, f'El asiento #{asiento_obj.numero} ya fue adquirido.')
                return redirect('detalle_servicio', servicio_id=servicio.id)

            pasajeros_validados.append({
                'asiento': asiento_obj,
                'nombre': nombre,
                'apellido': apellido,
                'rut': rut,
                'es_menor': es_menor
            })

        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)

        hay_menor_en_seleccion = any(p['es_menor'] for p in pasajeros_validados)
        hay_adulto_en_seleccion = any(not p['es_menor'] for p in pasajeros_validados)
        hay_adulto_en_carro_mismo_servicio = ItemCarro.objects.filter(
            carro=carro,
            servicio=servicio,
            es_menor=False
        ).exclude(asiento_id__in=asientos_ids).exists()

        if hay_menor_en_seleccion and not (hay_adulto_en_seleccion or hay_adulto_en_carro_mismo_servicio):
            messages.error(
                request,
                'No se permite que un menor de edad viaje solo. Debes incluir al menos un pasajero adulto en la compra para este recorrido.'
            )
            return redirect('detalle_servicio', servicio_id=servicio.id)

        agregados = 0
        for p in pasajeros_validados:
            ItemCarro.objects.update_or_create(
                carro=carro,
                servicio=servicio,
                asiento=p['asiento'],
                defaults={
                    'nombre_ocupante': p['nombre'],
                    'apellido_ocupante': p['apellido'],
                    'rut_ocupante': p['rut'],
                    'es_menor': p['es_menor']
                }
            )
            agregados += 1

        messages.success(request, f'Se agregaron {agregados} pasaje(s) al carro de compras.')
        return redirect('ver_carro')
        
    return render(request, 'servicio_detalle.html', {
        'servicio': servicio,
        'asientos': asientos,
        'ocupados': ocupados,
        'precio_semi': precio_semi,
        'precio_cama': precio_cama
    })

# ==============================================================================
# SECCIÓN 5: CARRO DE COMPRAS, CHECKOUT, PAGO Y MIS COMPRAS
# ==============================================================================
@login_required(login_url='login')
def ver_carro(request):
    carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
    items = carro.items.select_related('servicio', 'asiento', 'asiento__tipo_asiento').all()
    
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
    item = get_object_or_404(ItemCarro, carro=carro, id=item_id)
    servicio = item.servicio

    if not item.es_menor:
        otros_items_servicio = ItemCarro.objects.filter(carro=carro, servicio=servicio).exclude(id=item.id)
        hay_menores = otros_items_servicio.filter(es_menor=True).exists()
        hay_otros_adultos = otros_items_servicio.filter(es_menor=False).exists()
        if hay_menores and not hay_otros_adultos:
            messages.error(
                request,
                'No puedes quitar el pasaje del único adulto porque quedaría un menor de edad viajando solo en ese recorrido. Elimina primero el pasaje del menor.'
            )
            return redirect('ver_carro')

    item.delete()
    messages.info(request, 'Pasaje retirado del carro.')
    return redirect('ver_carro')

@login_required(login_url='login')
@transaction.atomic
def procesar_checkout(request):
    carro = get_object_or_404(CarroPasajes, usuario=request.user)
    items = list(carro.items.select_related('servicio', 'asiento', 'asiento__tipo_asiento').all())
    
    if not items:
        messages.error(request, 'Tu carro está vacío.')
        return redirect('home')

    servicios_en_carro = set(i.servicio for i in items)
    for srv in servicios_en_carro:
        items_srv = [i for i in items if i.servicio_id == srv.id]
        tiene_menores = any(i.es_menor for i in items_srv)
        tiene_adultos = any(not i.es_menor for i in items_srv)
        if tiene_menores and not tiene_adultos:
            messages.error(
                request,
                f'Error en recorrido {srv.origen.nombre} -> {srv.destino.nombre}: Un menor de edad no puede viajar solo sin un adulto acompañante.'
            )
            return redirect('ver_carro')

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
            apellido_pasajero=item.apellido_ocupante,
            rut_pasajero=item.rut_ocupante,
            es_menor=item.es_menor,
            precio_pagado=item.obtener_subtotal()
        )

    carro.items.all().delete()
    messages.info(request, 'Orden generada en estado PENDIENTE. Redirigiendo a Webpay para pagar.')
    return redirect('webpay_iniciar', venta_id=venta.id)

@login_required(login_url='login')
@transaction.atomic
def pagar_orden_web(request, venta_id):
    venta = get_object_or_404(Venta, id=venta_id, usuario=request.user)

    if venta.estado != 'PENDIENTE':
        messages.warning(request, f'La orden ya se encuentra en estado {venta.estado}.')
        primer_boleto = venta.boletos.first()
        return redirect('comprobante', boleto_id=primer_boleto.id)

    boletos = list(venta.boletos.select_related('servicio', 'asiento').all())
    asientos_ids = [b.asiento_id for b in boletos]

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
    boleto = get_object_or_404(Boleto, id=boleto_id)
    return render(request, 'comprobante.html', {
        'boleto': boleto,
        'venta': boleto.venta
    })

@login_required(login_url='login')
def mis_compras_view(request):
    ventas = Venta.objects.filter(usuario=request.user).prefetch_related(
        'boletos__servicio', 'boletos__asiento', 'boletos__asiento__tipo_asiento'
    ).order_by('-fecha_venta')
    return render(request, 'mis_compras.html', {'ventas': ventas})

# ==============================================================================
# SECCIÓN 6: PANEL ADMINISTRATIVO DE FLOTA (ADMIN_FLOTA)
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
                try:
                    ciudad.delete()
                    messages.success(request, f'Ciudad "{ciudad.nombre}" eliminada.')
                except models.ProtectedError:
                    messages.error(request, f'No se puede eliminar "{ciudad.nombre}": tiene registros protegidos asociados.')
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
                tipo_cama, _ = TipoAsiento.objects.get_or_create(
                    codigo='SALON_CAMA',
                    defaults={'nombre': 'Salón Cama', 'multiplicador_precio': Decimal('1.20')}
                )
                tipo_semi, _ = TipoAsiento.objects.get_or_create(
                    codigo='SEMICAMA',
                    defaults={'nombre': 'Semicama', 'multiplicador_precio': Decimal('1.00')}
                )
                bus = Bus.objects.create(patente=patente)
                asientos = [
                    Asiento(
                        bus=bus, 
                        numero=n, 
                        tipo_asiento=tipo_cama if n <= 10 else tipo_semi
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
                try:
                    bus.asientos.all().delete()
                    bus.delete()
                    messages.success(request, f'Bus {bus.patente} eliminado.')
                except models.ProtectedError:
                    messages.error(request, f'No se puede eliminar el bus {bus.patente}: tiene registros protegidos asociados.')
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

            if Boleto.objects.filter(servicio=servicio).exists():
                messages.error(request, f'No se puede eliminar el servicio #{servicio_id}: tiene boletos registrados asociados.')
            else:
                try:
                    ItemCarro.objects.filter(servicio=servicio).delete()
                    servicio.delete()
                    messages.success(request, f'Servicio #{servicio_id} eliminado exitosamente.')
                except models.ProtectedError:
                    messages.error(request, f'No se puede eliminar el servicio #{servicio_id}: la base de datos bloquea el borrado por registros protegidos.')
            return redirect('gestion_servicios')

    for s in servicios:
        s.fecha_salida_html = s.fecha_salida.strftime('%Y-%m-%dT%H:%M')

    return render(request, 'gestion.html', {
        'ciudades': ciudades,
        'buses': buses,
        'servicios': servicios,
        'ahora_html': ahora_html
    })

# ==============================================================================
# SECCIÓN 7: CONTROLADOR DE ERROR 404 PERSONALIZADO
# ==============================================================================
def error_404_view(request, exception=None):
    return render(request, '404.html', status=404)

# ==============================================================================
# SECCIÓN 8: INTEGRACIÓN TRANSBANK WEBPAY PLUS
# ==============================================================================
def webpay_iniciar(request, venta_id=None):
    """
    Inicia la transacción en Webpay Plus.
    Si se proporciona venta_id, utiliza el total real de la venta.
    Si no, usa un monto fijo de prueba de $5.000.
    """
    if venta_id:
        venta = get_object_or_404(Venta, id=venta_id)
        amount = int(venta.total)
        buy_order = f"ORDEN-{venta.id}"
    else:
        amount = 5000
        buy_order = str(random.randint(100000, 999999))

    session_id = str(random.randint(100000, 999999))
    return_url = 'https://andessur-b.duckdns.org/transporte/webpay-retorno/'

    tx = Transaction(WebpayOptions(
        IntegrationCommerceCodes.WEBPAY_PLUS,
        IntegrationApiKeys.WEBPAY,
        IntegrationType.TEST
    ))

    response = tx.create(buy_order, session_id, amount, return_url)

    return render(request, 'webpay_pagar.html', {
        'url': response['url'],
        'token': response['token'],
        'amount': amount,
        'buy_order': buy_order
    })

def webpay_retorno(request):
    """
    Recibe la respuesta de Transbank tras la interacción del usuario en la pasarela.
    """
    token = request.GET.get('token_ws') or request.POST.get('token_ws')

    if not token:
        return render(request, 'webpay_resultado.html', {
            'status': 'RECHAZADO',
            'mensaje': 'Compra cancelada por el usuario en el portal de Webpay.'
        })

    tx = Transaction(WebpayOptions(
        IntegrationCommerceCodes.WEBPAY_PLUS,
        IntegrationApiKeys.WEBPAY,
        IntegrationType.TEST
    ))

    response = tx.commit(token)

    if response.get('status') == 'AUTHORIZED' and response.get('response_code') == 0:
        # Si la orden vino asociada a una Venta del sistema, marcarla como PAGADA
        buy_order = response.get('buy_order', '')
        if buy_order.startswith('ORDEN-'):
            try:
                v_id = int(buy_order.replace('ORDEN-', ''))
                venta = Venta.objects.get(id=v_id)
                venta.estado = 'PAGADO'
                venta.save()
            except (Venta.DoesNotExist, ValueError):
                pass

        return render(request, 'webpay_resultado.html', {
            'status': 'APROBADO',
            'response': response
        })
    else:
        return render(request, 'webpay_resultado.html', {
            'status': 'RECHAZADO',
            'response': response
        })