"""
==============================================================================
MÓDULO DE VISTAS DE API REST (transporte/api_views.py)
------------------------------------------------------------------------------
Endpoints RESTful con DRF, JWT con claims de rol RBAC, ciclo de vida de venta:
PENDIENTE -> PAGADO -> ENTREGADO / CANCELADO, concurrencia con select_for_update 
en Asiento al momento de pagar, y filtros avanzados con django-filter.
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
    """Agrega claim de rol personalizado al payload JWT."""
    @classmethod
    def get_token(cls, user):
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
    """Endpoint /api/token/."""
    serializer_class = CustomTokenObtainPairSerializer

# ==============================================================================
# PERMISOS RBAC
# ==============================================================================
class EsAdminFlota(permissions.BasePermission):
    """Permiso exclusivo para administradores de flota."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_staff or 
            (hasattr(request.user, 'perfil') and user_rol_admin(request.user))
        )

def user_rol_admin(user):
    return hasattr(user, 'perfil') and user.perfil.rol == 'ADMIN_FLOTA'

# ==============================================================================
# FILTROS Y CONSULTA DE SERVICIOS
# ==============================================================================
class ServicioFilter(django_filters.FilterSet):
    precio_min = django_filters.NumberFilter(field_name='precio_base', lookup_expr='gte')
    precio_max = django_filters.NumberFilter(field_name='precio_base', lookup_expr='lte')
    fecha_desde = django_filters.DateTimeFilter(field_name='fecha_salida', lookup_expr='gte')

    class Meta:
        model = Servicio
        fields = ['origen', 'destino', 'precio_min', 'precio_max', 'fecha_desde']

class BuscarServiciosAPI(generics.ListAPIView):
    """Consulta de recorridos con filtros por origen, destino, precio y fecha."""
    queryset = Servicio.objects.all().order_by('fecha_salida')
    serializer_class = ServicioSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ServicioFilter

ServiciosListAPI = BuscarServiciosAPI

class AsientosServicioAPI(APIView):
    """Mapa de asientos con estado de disponibilidad en tiempo real."""
    @extend_schema(responses={200: AsientoSerializer(many=True)})
    def get(self, request, servicio_id):
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
# HISTORIAL DE COMPRAS
# ==============================================================================
class MisBoletosAPI(generics.ListAPIView):
    """Historial de boletos pagados o entregados del pasajero."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BoletoSerializer

    def get_queryset(self):
        return Boleto.objects.filter(
            venta__usuario=self.request.user,
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).order_by('-id')

# ==============================================================================
# GESTIÓN CRUD DE SERVICIOS (ADMIN)
# ==============================================================================
class GestionServiciosAPI(generics.ListCreateAPIView):
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all().order_by('-fecha_salida')
    serializer_class = ServicioSerializer

GestionServicioAPI = GestionServiciosAPI

class GestionServiciosDetalleAPI(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def perform_destroy(self, instance):
        if Boleto.objects.filter(servicio=instance, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            raise ValidationError({'error': 'No se puede eliminar servicio con boletos pagados o entregados.'})
        instance.delete()

GestionServicioDetalleAPI = GestionServiciosDetalleAPI

# ==============================================================================
# CARRO, CHECKOUT (PENDIENTE) Y PAGO CON BLOQUEO PESIMISTA
# ==============================================================================
class CarroPasajesAPI(APIView):
    """Gestión de carro de compras."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: CarroPasajesSerializer})
    def get(self, request):
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        return Response(CarroPasajesSerializer(carro).data)

    @extend_schema(request=AgregarCarroInputSerializer, responses={201: CarroPasajesSerializer})
    def post(self, request):
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
            return Response({'error': 'El asiento ya fue adquirido.'}, status=status.HTTP_400_BAD_REQUEST)

        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        ItemCarro.objects.update_or_create(
            carro=carro, servicio=servicio, asiento=asiento,
            defaults={'nombre_ocupante': data['nombre'], 'rut_ocupante': data['rut']}
        )
        return Response(CarroPasajesSerializer(carro).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def delete(self, request):
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        carro.items.all().delete()
        return Response({'mensaje': 'Carro vaciado.'}, status=status.HTTP_204_NO_CONTENT)

class CheckoutAPI(APIView):
    """
    PASO 1: Checkout.
    Genera la venta en estado PENDIENTE con sus boletos asociados y vacía el carro.
    No descuenta cupo todavía (el cupo se valida y descuenta al momento del pago).
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={201: VentaSerializer})
    @transaction.atomic
    def post(self, request):
        carro = get_object_or_404(CarroPasajes, usuario=request.user)
        items = list(carro.items.select_related('servicio', 'asiento').all())

        if not items:
            return Response({'error': 'El carro está vacío.'}, status=status.HTTP_400_BAD_REQUEST)

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
    PASO 2: Confirmación de Pago.
    Pasa una venta de PENDIENTE a PAGADO.
    Aplica bloqueo pesimista con select_for_update sobre los asientos involucrados.
    Si algún asiento ya fue pagado por otro usuario, se aborta y se rechaza.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: VentaSerializer})
    @transaction.atomic
    def post(self, request, pk):
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

        # Validación atómica de disponibilidad real
        for b in boletos:
            if Boleto.objects.filter(
                servicio=b.servicio,
                asiento=b.asiento,
                venta__estado__in=['PAGADO', 'ENTREGADO']
            ).exclude(venta=venta).exists():
                return Response(
                    {'error': f'El asiento #{b.asiento.numero} ya fue adquirido por otro usuario.'},
                    status=status.HTTP_409_CONFLICT
                )

        venta.estado = 'PAGADO'
        venta.save()
        return Response(VentaSerializer(venta).data, status=status.HTTP_200_OK)

class CancelarVentaAPI(APIView):
    """
    Cancela una venta del usuario autenticado (desde PENDIENTE o PAGADO),
    liberando inmediatamente los asientos.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: VentaSerializer})
    @transaction.atomic
    def post(self, request, pk):
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
# CAMBIO DE ESTADOS ADMINISTRATIVO
# ==============================================================================
class CambiarEstadoVentaAPI(APIView):
    """
    Transición de estados por parte del Administrador de Flota.
    Valida los saltos de estado permitidos:
    - PENDIENTE -> PAGADO o CANCELADO
    - PAGADO -> ENTREGADO o CANCELADO
    """
    permission_classes = [EsAdminFlota]

    @extend_schema(request=CambiarEstadoSerializer, responses={200: VentaSerializer})
    @transaction.atomic
    def patch(self, request, pk):
        venta = get_object_or_404(Venta, id=pk)
        serializer = CambiarEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nuevo_estado = serializer.validated_data['estado']

        # Validaciones de transiciones lógicas
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
                        {'error': f'Conflicto: El asiento #{b.asiento.numero} ya está ocupado por otra orden.'},
                        status=status.HTTP_409_CONFLICT
                    )

        venta.estado = nuevo_estado
        venta.save()
        return Response(VentaSerializer(venta).data)