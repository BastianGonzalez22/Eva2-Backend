"""
MÓDULO API REST (transporte/api_views.py)
Endpoints RESTful con JWT (claims personalizados con rol), filtrado,
transacciones atómicas y documentación OpenAPI (Swagger).
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
    """Agrega el claim 'rol' dentro del payload del token JWT de acceso."""
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
    """Vista de login JWT que emite tokens con el rol incorporado."""
    serializer_class = CustomTokenObtainPairSerializer

# ==============================================================================
# PERMISOS RBAC
# ==============================================================================
class EsAdminFlota(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_staff or 
            (hasattr(request.user, 'perfil') and request.user.perfil.rol == 'ADMIN_FLOTA')
        )

# ==============================================================================
# ENDPOINTS PÚBLICOS DE CONSULTA Y BÚSQUEDA
# ==============================================================================
class BuscarServiciosAPI(generics.ListAPIView):
    """Búsqueda y listado público de servicios con soporte de filtros."""
    queryset = Servicio.objects.all().order_by('fecha_salida')
    serializer_class = ServicioSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['origen', 'destino']

ServiciosListAPI = BuscarServiciosAPI

class AsientosServicioAPI(APIView):
    """Retorna los asientos de un servicio indicando cuáles están disponibles u ocupados."""
    
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
    """Lista todos los boletos comprados por el usuario autenticado."""
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
    """Listar y crear recorridos para administradores."""
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all().order_by('-fecha_salida')
    serializer_class = ServicioSerializer

GestionServicioAPI = GestionServiciosAPI

class GestionServiciosDetalleAPI(generics.RetrieveUpdateDestroyAPIView):
    """Obtener, editar o eliminar un recorrido específico."""
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def perform_destroy(self, instance):
        # En DRF se debe levantar ValidationError para detener el borrado y devolver 400
        if Boleto.objects.filter(servicio=instance, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
            raise ValidationError({'error': 'No se puede eliminar el servicio porque posee boletos pagados asociados.'})
        instance.delete()

GestionServicioDetalleAPI = GestionServiciosDetalleAPI

# ==============================================================================
# CARRO DE COMPRAS Y CHECKOUT TRANSACCIONAL
# ==============================================================================
class CarroPasajesAPI(APIView):
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
            return Response({'error': 'El asiento ya fue adquirido.'}, status=status.HTTP_400_BAD_REQUEST)

        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        ItemCarro.objects.update_or_create(
            carro=carro, servicio=servicio, asiento=asiento,
            defaults={'nombre_ocupante': data['nombre'], 'rut_ocupante': data['rut']}
        )
        return Response(CarroPasajesSerializer(carro).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def delete(self, request):
        """Vacía el carro de compras del cliente (requerido por la matriz)."""
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        carro.items.all().delete()
        return Response({'mensaje': 'Carro vaciado exitosamente.'}, status=status.HTTP_204_NO_CONTENT)

class CheckoutAPI(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={201: VentaSerializer})
    @transaction.atomic
    def post(self, request):
        carro = get_object_or_404(CarroPasajes, usuario=request.user)
        items = carro.items.select_for_update().select_related('servicio', 'asiento').all()

        if not items.exists():
            return Response({'error': 'El carro está vacío.'}, status=status.HTTP_400_BAD_REQUEST)

        for item in items:
            if Boleto.objects.filter(
                servicio=item.servicio, 
                asiento=item.asiento, 
                venta__estado__in=['PAGADO', 'ENTREGADO']
            ).exists():
                return Response(
                    {'error': f'El asiento #{item.asiento.numero} ya fue comprado.'}, 
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
    permission_classes = [EsAdminFlota]

    @extend_schema(request=CambiarEstadoSerializer, responses={200: VentaSerializer})
    @transaction.atomic
    def patch(self, request, pk):
        venta = get_object_or_404(Venta, id=pk)
        serializer = CambiarEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nuevo_estado = serializer.validated_data['estado']

        if venta.estado == 'CANCELADO' and nuevo_estado in ['PAGADO', 'ENTREGADO']:
            for b in venta.boletos.all():
                if Boleto.objects.filter(
                    servicio=b.servicio, 
                    asiento=b.asiento, 
                    venta__estado__in=['PAGADO', 'ENTREGADO']
                ).exclude(venta=venta).exists():
                    return Response(
                        {'error': f'No se puede reactivar: el asiento #{b.asiento.numero} fue vendido a otro usuario.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        venta.estado = nuevo_estado
        venta.save()
        return Response(VentaSerializer(venta).data)