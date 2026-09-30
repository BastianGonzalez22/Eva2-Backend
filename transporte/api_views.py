"""
==============================================================================
MÓDULO DE VISTAS DE API REST (transporte/api_views.py)
------------------------------------------------------------------------------
Endpoints RESTful bajo Django REST Framework:
- Autenticación JWT con claim de rol RBAC.
- Catálogo y búsqueda con filtros (origen, destino, precio, fecha).
- Carro de compras persistente por usuario.
- Ciclo de venta: PENDIENTE -> PAGADO -> ENTREGADO / CANCELADO.
- Bloqueo pesimista con select_for_update en Asiento contra sobreventa.
- CRUD administrativo de servicios con control estricto de integridad.
==============================================================================
"""

from rest_framework import generics, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.db import transaction
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
import django_filters
from drf_spectacular.utils import extend_schema

from .models import Servicio, Asiento, Boleto, Venta, CarroPasajes, ItemCarro, validar_rut_chileno
from .serializers import (
    ServicioSerializer, AsientoSerializer, VentaSerializer, BoletoSerializer,
    CarroPasajesSerializer, CambiarEstadoSerializer, AgregarCarroInputSerializer
)


# ==============================================================================
# AUTENTICACIÓN JWT CON CLAIM PERSONALIZADO DE ROL
# ==============================================================================
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Serializador JWT que inyecta los claims 'rol' y 'username' en el payload
    del token de acceso para permitir control de acceso RBAC en clientes REST.
    """
    @classmethod
    def get_token(cls, user):
        """
        Sobrescribe la generación del token incorporando el claim 'rol'.
        """
        token = super().get_token(user)
        rol = 'PASAJERO'
        if user.is_staff:
            rol = 'ADMIN_FLOTA'
        elif hasattr(user, 'perfil'):
            rol = user.perfil.rol
        token['rol'] = rol
        token['username'] = user.username
        return token


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Vista de autenticación que expone /api/token/ generando tokens JWT
    enriquecidos con los datos del rol del usuario.
    """
    serializer_class = CustomTokenObtainPairSerializer


# ==============================================================================
# PERMISOS RBAC
# ==============================================================================
class EsAdminFlota(permissions.BasePermission):
    """
    Permiso de autorización RBAC que concede acceso únicamente a usuarios
    autenticados con categoría is_staff o con rol ADMIN_FLOTA en su perfil.
    """
    def has_permission(self, request, view):
        """
        Evalúa si el usuario solicitante posee permisos de administración.
        """
        return request.user.is_authenticated and (
            request.user.is_staff or 
            (hasattr(request.user, 'perfil') and request.user.perfil.rol == 'ADMIN_FLOTA')
        )


# ==============================================================================
# FILTRO AVANZADO DE SERVICIOS
# ==============================================================================
class ServicioFilter(django_filters.FilterSet):
    """
    Filtro multidimensional para servicios usando django-filter:
    - origen: ID de la ciudad de origen.
    - destino: ID de la ciudad de destino.
    - precio_min: Tarifa base mayor o igual al valor provisto.
    - precio_max: Tarifa base menor o igual al valor provisto.
    - fecha_desde: Salidas programadas a partir de la fecha y hora dada.
    """
    precio_min = django_filters.NumberFilter(field_name='precio_base', lookup_expr='gte')
    precio_max = django_filters.NumberFilter(field_name='precio_base', lookup_expr='lte')
    fecha_desde = django_filters.DateTimeFilter(field_name='fecha_salida', lookup_expr='gte')

    class Meta:
        model = Servicio
        fields = ['origen', 'destino', 'precio_min', 'precio_max', 'fecha_desde']


# ==============================================================================
# ENDPOINTS PÚBLICOS DE CONSULTA Y BÚSQUEDA
# ==============================================================================
class BuscarServiciosAPI(generics.ListAPIView):
    """
    GET /api/servicios/buscar/
    Consulta pública de itinerarios vigentes con filtrado por origen,
    destino, rango tarifario y fecha de salida.
    """
    queryset = Servicio.objects.all().order_by('fecha_salida')
    serializer_class = ServicioSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ServicioFilter


class AsientosServicioAPI(APIView):
    """
    GET /api/servicios/<id>/asientos/
    Retorna el inventario de butacas para un servicio, indicando si cada
    asiento está ocupado (en ventas PAGADO/ENTREGADO) y su tarifa calculada.
    """
    @extend_schema(responses={200: AsientoSerializer(many=True)})
    def get(self, request, servicio_id):
        """
        Construye y retorna el listado de butacas con disponibilidad y precio.
        """
        servicio = get_object_or_404(Servicio, id=servicio_id)
        asientos = servicio.bus.asientos.all().order_by('numero')
        
        ocupados = Boleto.objects.filter(
            servicio=servicio,
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).values_list('asiento_id', flat=True)
        
        data = []
        for a in asientos:
            data.append({
                'id': a.id,
                'numero': a.numero,
                'tipo': a.tipo,
                'precio': a.calcular_precio(servicio.precio_base),
                'ocupado': a.id in ocupados
            })
        return Response(data)


# ==============================================================================
# HISTORIAL DE COMPRAS DEL PASAJERO
# ==============================================================================
class MisBoletosAPI(generics.ListAPIView):
    """
    GET /api/mis-boletos/
    Retorna el catálogo histórico de boletos válidos pertenecientes al
    pasajero autenticado vía JWT.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BoletoSerializer

    def get_queryset(self):
        """
        Filtra boletos asociados a órdenes PAGADO o ENTREGADO del usuario activo.
        """
        return Boleto.objects.filter(
            venta__usuario=self.request.user,
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).order_by('-id')


# ==============================================================================
# GESTIÓN CRUD DE SERVICIOS (ADMIN_FLOTA)
# ==============================================================================
class GestionServiciosAPI(generics.ListCreateAPIView):
    """
    GET / POST /api/servicios/
    Endpoint de administración:
    - GET: Lista la totalidad de recorridos programados.
    - POST: Publica un nuevo recorrido en la base de datos.
    """
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all().order_by('-fecha_salida')
    serializer_class = ServicioSerializer


class GestionServiciosDetalleAPI(generics.RetrieveUpdateDestroyAPIView):
    """
    GET / PUT / PATCH / DELETE /api/servicios/<id>/
    Endpoint de administración:
    - GET: Detalle de un recorrido.
    - PUT/PATCH: Modificación de itinerario o tarifas.
    - DELETE: Eliminación con protección de integridad (impide borrar si tiene boletos).
    """
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def perform_destroy(self, instance):
        """
        Valida que el servicio no posea NINGÚN boleto asociado (sea pendiente,
        pagado, entregado o cancelado) antes de permitir su eliminación para
        evitar excepciones de ProtectedError en la base de datos.
        """
        if Boleto.objects.filter(servicio=instance).exists():
            raise ValidationError({'error': 'No se puede eliminar: el recorrido tiene boletos registrados asociados.'})
        
        # Limpia posibles pasajes temporales en carros antes de borrar
        ItemCarro.objects.filter(servicio=instance).delete()
        instance.delete()


# ==============================================================================
# CARRO DE COMPRAS, CHECKOUT Y PAGO CON BLOQUEO PESIMISTA
# ==============================================================================
class CarroPasajesAPI(APIView):
    """
    Gestión del carro de compras persistente:
    - GET: Recupera el estado actual y desgloses de asientos agregados.
    - POST: Añade un asiento con verificación algorítmica de RUT chileno.
    - DELETE: Vacía la totalidad de pasajes contenidos en el carro.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: CarroPasajesSerializer})
    def get(self, request):
        """
        Recupera y serializa el carro de compras del usuario autenticado.
        """
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        return Response(CarroPasajesSerializer(carro).data)

    @extend_schema(request=AgregarCarroInputSerializer, responses={201: CarroPasajesSerializer})
    def post(self, request):
        """
        Valida el RUT del ocupante y agrega el asiento al carro del usuario.
        """
        serializer = AgregarCarroInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        try:
            validar_rut_chileno(data['rut'])
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        servicio = get_object_or_404(Servicio, id=data['servicio_id'])
        asiento = get_object_or_404(Asiento, id=data['asiento_id'], bus=servicio.bus)

        if Boleto.objects.filter(servicio=servicio, asiento=asiento, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            return Response({'error': 'El asiento ya se encuentra adquirido.'}, status=status.HTTP_400_BAD_REQUEST)

        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        ItemCarro.objects.update_or_create(
            carro=carro, servicio=servicio, asiento=asiento,
            defaults={'nombre_ocupante': data['nombre'], 'rut_ocupante': data['rut']}
        )
        return Response(CarroPasajesSerializer(carro).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def delete(self, request):
        """
        Elimina todos los ítems del carro de compras del usuario autenticado.
        """
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        carro.items.all().delete()
        return Response({'mensaje': 'Carro vaciado correctamente.'}, status=status.HTTP_204_NO_CONTENT)


class CheckoutAPI(APIView):
    """
    POST /api/ventas/checkout/
    Inicia la transacción creando la Venta en estado PENDIENTE con sus Boletos.
    Vacía el carro sin bloquear definitivamente el asiento para los demás.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={201: VentaSerializer})
    @transaction.atomic
    def post(self, request):
        """
        Ejecuta el checkout creando la orden en estado PENDIENTE.
        """
        carro = get_object_or_404(CarroPasajes, usuario=request.user)
        items = list(carro.items.select_related('servicio', 'asiento').all())

        if not items:
            return Response({'error': 'El carro de compras está vacío.'}, status=status.HTTP_400_BAD_REQUEST)

        total = sum(item.obtener_subtotal() for item in items)
        venta = Venta.objects.create(
            usuario=request.user,
            total=total,
            estado='PENDIENTE'
        )

        for item in items:
            Boleto.objects.create(
                venta=venta,
                servicio=item.servicio,
                asiento=item.asiento,
                nombre_pasajero=item.nombre_ocupante,
                rut_pasajero=item.rut_ocupante,
                precio_pagado=item.obtener_subtotal()
            )

        carro.items.all().delete()
        return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)


class PagarVentaAPI(APIView):
    """
    POST /api/ventas/<id>/pagar/
    Confirma el pago pasando la venta de PENDIENTE a PAGADO.
    Aplica bloqueo pesimista select_for_update sobre los registros de Asiento.
    Si algún asiento ya fue pagado por otra transacción, aborta con HTTP 409 Conflict.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: VentaSerializer})
    @transaction.atomic
    def post(self, request, pk):
        """
        Ejecuta la confirmación atómica del pago con bloqueo de butacas.
        """
        venta = get_object_or_404(Venta, id=pk, usuario=request.user)

        if venta.estado != 'PENDIENTE':
            return Response(
                {'error': f'Solo se pueden pagar ventas en estado PENDIENTE. Estado actual: {venta.estado}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        boletos = list(venta.boletos.select_related('servicio', 'asiento').all())
        asientos_ids = [b.asiento_id for b in boletos]

        # Bloqueo pesimista a nivel de fila sobre las butacas
        list(Asiento.objects.filter(id__in=asientos_ids).select_for_update())

        # Validación de disponibilidad atómica
        for b in boletos:
            if Boleto.objects.filter(
                servicio=b.servicio,
                asiento=b.asiento,
                venta__estado__in=['PAGADO', 'ENTREGADO']
            ).exclude(venta=venta).exists():
                return Response(
                    {'error': f'Conflicto: El asiento #{b.asiento.numero} ya fue adquirido por otro usuario.'},
                    status=status.HTTP_409_CONFLICT
                )

        venta.estado = 'PAGADO'
        venta.save()
        return Response(VentaSerializer(venta).data, status=status.HTTP_200_OK)


class CancelarVentaAPI(APIView):
    """
    POST /api/ventas/<id>/cancelar/
    Permite anular una venta en estado PENDIENTE o PAGADO perteneciente al
    usuario autenticado, liberando inmediatamente las butacas asociadas.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: VentaSerializer})
    @transaction.atomic
    def post(self, request, pk):
        """
        Ejecuta la anulación y liberación de butacas de la orden.
        """
        venta = get_object_or_404(Venta, id=pk, usuario=request.user)

        if venta.estado not in ['PENDIENTE', 'PAGADO']:
            return Response(
                {'error': f'No se puede cancelar una venta en estado {venta.estado}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        venta.estado = 'CANCELADO'
        venta.save()
        return Response(VentaSerializer(venta).data, status=status.HTTP_200_OK)


# ==============================================================================
# TRANSICIÓN ADMINISTRATIVA DE ESTADOS
# ==============================================================================
class CambiarEstadoVentaAPI(APIView):
    """
    PATCH /api/ventas/<id>/estado/
    Permite al ADMIN_FLOTA cambiar el estado de una venta respetando el
    flujo permitido del ciclo de vida del negocio:
    - PENDIENTE -> PAGADO o CANCELADO
    - PAGADO -> ENTREGADO o CANCELADO
    """
    permission_classes = [EsAdminFlota]

    @extend_schema(request=CambiarEstadoSerializer, responses={200: VentaSerializer})
    @transaction.atomic
    def patch(self, request, pk):
        """
        Valida y aplica la transición de estado solicitada por el administrador.
        """
        venta = get_object_or_404(Venta, id=pk)
        serializer = CambiarEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nuevo_estado = serializer.validated_data['estado']

        transiciones_validas = {
            'PENDIENTE': ['PAGADO', 'CANCELADO'],
            'PAGADO': ['ENTREGADO', 'CANCELADO'],
            'ENTREGADO': [],
            'CANCELADO': []
        }

        if nuevo_estado not in transiciones_validas.get(venta.estado, []):
            return Response(
                {'error': f'Transición no permitida: de {venta.estado} a {nuevo_estado}.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if nuevo_estado in ['PAGADO', 'ENTREGADO']:
            asientos_ids = [b.asiento_id for b in venta.boletos.all()]
            list(Asiento.objects.filter(id__in=asientos_ids).select_for_update())

            for b in venta.boletos.all():
                if Boleto.objects.filter(
                    servicio=b.servicio,
                    asiento=b.asiento,
                    venta__estado__in=['PAGADO', 'ENTREGADO']
                ).exclude(venta=venta).exists():
                    return Response(
                        {'error': f'Conflicto: El asiento #{b.asiento.numero} ya se encuentra ocupado.'},
                        status=status.HTTP_409_CONFLICT
                    )

        venta.estado = nuevo_estado
        venta.save()
        return Response(VentaSerializer(venta).data)