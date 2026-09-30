"""
==============================================================================
MÓDULO DE VISTAS DE API REST (transporte/api_views.py)
------------------------------------------------------------------------------
Implementa endpoints RESTful bajo arquitectura DRF, autenticación JWT con 
claims personalizados de control de acceso RBAC, transacciones atómicas con 
bloqueo pesimista a nivel de fila (select_for_update) sobre la entidad Asiento 
para garantizar concurrencia libre de condiciones de carrera, y filtros 
avanzados vía DjangoFilterBackend.
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
    Serializador JWT que inyecta en el payload decodificable del token de acceso
    el claim personalizado 'rol' ('PASAJERO' o 'ADMIN_FLOTA') para control RBAC.
    """
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
    """
    Vista de autenticación que expone el endpoint /api/token/ emitiendo tokens
    con la estructura de claims extendida para la arquitectura del sistema.
    """
    serializer_class = CustomTokenObtainPairSerializer

# ==============================================================================
# PERMISOS RBAC
# ==============================================================================
class EsAdminFlota(permissions.BasePermission):
    """
    Clase de autorización RBAC que restringe el acceso exclusivamente a usuarios
    con credenciales de staff o perfil verificado como ADMIN_FLOTA.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_staff or 
            (hasattr(request.user, 'perfil') and request.user.perfil.rol == 'ADMIN_FLOTA')
        )

# ==============================================================================
# FILTRO AVANZADO DE SERVICIOS
# ==============================================================================
class ServicioFilter(django_filters.FilterSet):
    """
    Filtro avanzado para itinerarios: soporta origen, destino, rango de fechas
    y rango de precios mínimos y máximos mediante django-filter.
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
    Consulta pública de recorridos disponibles con filtrado multidimensional
    por origen, destino, rango de precios y fecha de salida.
    """
    queryset = Servicio.objects.all().order_by('fecha_salida')
    serializer_class = ServicioSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ServicioFilter

ServiciosListAPI = BuscarServiciosAPI

class AsientosServicioAPI(APIView):
    """
    Inspección del mapa de asientos de un recorrido. Calcula el precio según la
    categoría del asiento e informa disponibilidad en tiempo real.
    """
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
# HISTORIAL DE BOLETOS DEL PASAJERO
# ==============================================================================
class MisBoletosAPI(generics.ListAPIView):
    """
    Retorna el historial de compras y boletos emitidos pertenecientes al
    usuario autenticado con sesión JWT activa.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BoletoSerializer

    def get_queryset(self):
        return Boleto.objects.filter(
            venta__usuario=self.request.user,
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).order_by('-id')

# ==============================================================================
# GESTIÓN ADMINISTRATIVA DE SERVICIOS (ADMIN_FLOTA)
# ==============================================================================
class GestionServiciosAPI(generics.ListCreateAPIView):
    """
    Endpoint administrativo para listar y dar de alta nuevos recorridos e
    itinerarios en la base de datos de transporte.
    """
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all().order_by('-fecha_salida')
    serializer_class = ServicioSerializer

GestionServicioAPI = GestionServiciosAPI

class GestionServiciosDetalleAPI(generics.RetrieveUpdateDestroyAPIView):
    """
    Endpoint administrativo para recuperar, modificar o eliminar recorridos.
    Implementa restricción de integridad impidiendo borrar recorridos con ventas.
    """
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def perform_destroy(self, instance):
        if Boleto.objects.filter(servicio=instance, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            raise ValidationError({'error': 'No se puede eliminar: el recorrido tiene boletos pagados o entregados.'})
        instance.delete()

GestionServicioDetalleAPI = GestionServiciosDetalleAPI

# ==============================================================================
# CARRO DE COMPRAS Y CHECKOUT TRANSACCIONAL
# ==============================================================================
class CarroPasajesAPI(APIView):
    """
    Gestión del carro de compras persistente por usuario:
    - GET: Recupera el estado y desglose de pasajes agregados.
    - POST: Agrega un asiento al carro con validación de RUT chileno.
    - DELETE: Vacía la totalidad de ítems del carro del usuario.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: CarroPasajesSerializer})
    def get(self, request):
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        serializer = CarroPasajesSerializer(carro)
        return Response(serializer.data)

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
            return Response({'error': 'El asiento ya se encuentra adquirido.'}, status=status.HTTP_400_BAD_REQUEST)

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
        return Response({'mensaje': 'Carro vaciado correctamente.'}, status=status.HTTP_204_NO_CONTENT)

class CheckoutAPI(APIView):
    """
    Checkout transaccional atómico:
    Aplica bloqueo pesimista select_for_update directamente sobre los registros
    físicos de la tabla Asiento para impedir colisiones o sobreventa en concurrencia.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={201: VentaSerializer})
    @transaction.atomic
    def post(self, request):
        carro = get_object_or_404(CarroPasajes, usuario=request.user)
        items = list(carro.items.select_related('servicio', 'asiento').all())

        if not items:
            return Response({'error': 'El carro está vacío.'}, status=status.HTTP_400_BAD_REQUEST)

        # Bloqueo pesimista sobre las butacas reales para prevenir condiciones de carrera
        asientos_ids = [item.asiento_id for item in items]
        list(Asiento.objects.filter(id__in=asientos_ids).select_for_update())

        # Verificación de integridad: asegurar que ningún asiento haya sido adquirido
        for item in items:
            if Boleto.objects.filter(
                servicio=item.servicio, 
                asiento=item.asiento, 
                venta__estado__in=['PAGADO', 'ENTREGADO']
            ).exists():
                return Response(
                    {'error': f'El asiento #{item.asiento.numero} ya fue adquirido por otro usuario.'}, 
                    status=status.HTTP_409_CONFLICT
                )

        total = sum(item.obtener_subtotal() for item in items)
        venta = Venta.objects.create(usuario=request.user, total=total, estado='PAGADO')

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

# ==============================================================================
# GESTIÓN Y TRANSICIÓN DE ESTADOS DE VENTA
# ==============================================================================
class CambiarEstadoVentaAPI(APIView):
    """
    Transición de estados de compra por parte de administradores de flota.
    Permite transicionar entre PAGADO, ENTREGADO y CANCELADO garantizando
    consistencia referencial sobre las butacas involucradas.
    """
    permission_classes = [EsAdminFlota]

    @extend_schema(request=CambiarEstadoSerializer, responses={200: VentaSerializer})
    @transaction.atomic
    def patch(self, request, pk):
        venta = get_object_or_404(Venta, id=pk)
        serializer = CambiarEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nuevo_estado = serializer.validated_data['estado']

        # Si una venta cancelada se reactiva, bloquear asientos y verificar disponibilidad
        if venta.estado == 'CANCELADO' and nuevo_estado in ['PAGADO', 'ENTREGADO']:
            asientos_ids = [b.asiento_id for b in venta.boletos.all()]
            list(Asiento.objects.filter(id__in=asientos_ids).select_for_update())

            for b in venta.boletos.all():
                if Boleto.objects.filter(
                    servicio=b.servicio, 
                    asiento=b.asiento, 
                    venta__estado__in=['PAGADO', 'ENTREGADO']
                ).exclude(venta=venta).exists():
                    return Response(
                        {'error': f'Conflicto: El asiento #{b.asiento.numero} ya fue adquirido por otra orden.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        venta.estado = nuevo_estado
        venta.save()
        return Response(VentaSerializer(venta).data)