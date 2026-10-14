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

from .models import (
    TipoAsiento, PerfilUsuario, Servicio, Asiento, Boleto, 
    Venta, CarroPasajes, ItemCarro, validar_rut_chileno
)
from .serializers import (
    TipoAsientoSerializer, PerfilUsuarioSerializer, ServicioSerializer, 
    AsientoSerializer, VentaSerializer, BoletoSerializer,
    CarroPasajesSerializer, CambiarEstadoSerializer, AgregarCarroInputSerializer
)


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
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
    serializer_class = CustomTokenObtainPairSerializer


class EsAdminFlota(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_staff or 
            (hasattr(request.user, 'perfil') and request.user.perfil.rol == 'ADMIN_FLOTA')
        )


class TipoAsientoListCreateAPI(generics.ListCreateAPIView):
    queryset = TipoAsiento.objects.all()
    serializer_class = TipoAsientoSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        return [EsAdminFlota()]


class TipoAsientoDetalleAPI(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [EsAdminFlota]
    queryset = TipoAsiento.objects.all()
    serializer_class = TipoAsientoSerializer


class MiPerfilAPI(generics.RetrieveUpdateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PerfilUsuarioSerializer

    def get_object(self):
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=self.request.user)
        return perfil


class ServicioFilter(django_filters.FilterSet):
    precio_min = django_filters.NumberFilter(field_name='precio_base', lookup_expr='gte')
    precio_max = django_filters.NumberFilter(field_name='precio_base', lookup_expr='lte')
    fecha_desde = django_filters.DateTimeFilter(field_name='fecha_salida', lookup_expr='gte')

    class Meta:
        model = Servicio
        fields = ['origen', 'destino', 'precio_min', 'precio_max', 'fecha_desde']


class BuscarServiciosAPI(generics.ListAPIView):
    queryset = Servicio.objects.all().order_by('fecha_salida')
    serializer_class = ServicioSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ServicioFilter


class AsientosServicioAPI(APIView):
    @extend_schema(responses={200: AsientoSerializer(many=True)})
    def get(self, request, servicio_id):
        servicio = get_object_or_404(Servicio, id=servicio_id)
        asientos = servicio.bus.asientos.select_related('tipo_asiento').all().order_by('numero')
        
        ocupados = Boleto.objects.filter(
            servicio=servicio,
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).values_list('asiento_id', flat=True)
        
        data = []
        for a in asientos:
            data.append({
                'id': a.id,
                'numero': a.numero,
                'tipo_asiento': a.tipo_asiento_id,
                'tipo': a.tipo,
                'tipo_nombre': a.get_tipo_display(),
                'precio': a.calcular_precio(servicio.precio_base),
                'ocupado': a.id in ocupados
            })
        return Response(data)


class MisBoletosAPI(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BoletoSerializer

    def get_queryset(self):
        return Boleto.objects.filter(
            venta__usuario=self.request.user,
            venta__estado__in=['PAGADO', 'ENTREGADO']
        ).order_by('-id')


class GestionServiciosAPI(generics.ListCreateAPIView):
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all().order_by('-fecha_salida')
    serializer_class = ServicioSerializer


class GestionServiciosDetalleAPI(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [EsAdminFlota]
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def perform_destroy(self, instance):
        if Boleto.objects.filter(servicio=instance).exists():
            raise ValidationError({'error': 'No se puede eliminar: el recorrido tiene boletos registrados asociados.'})
        ItemCarro.objects.filter(servicio=instance).delete()
        instance.delete()


class CarroPasajesAPI(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: CarroPasajesSerializer})
    def get(self, request):
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        return Response(CarroPasajesSerializer(carro).data)

    @extend_schema(request=AgregarCarroInputSerializer, responses={201: CarroPasajesSerializer})
    @transaction.atomic
    def post(self, request):
        serializer = AgregarCarroInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        servicio = get_object_or_404(Servicio, id=data['servicio_id'])
        lista_pasajeros = data.get('pasajeros')

        if not lista_pasajeros:
            if not data.get('asiento_id') or not data.get('nombre') or not data.get('rut'):
                return Response(
                    {'error': 'Debes enviar la lista "pasajeros" o los datos individuales (asiento_id, nombre, apellido, rut).'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            lista_pasajeros = [{
                'asiento_id': data['asiento_id'],
                'nombre': data['nombre'],
                'apellido': data.get('apellido', ''),
                'rut': data['rut'],
                'es_menor': data.get('es_menor', False)
            }]

        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        asientos_ids_enviados = [p['asiento_id'] for p in lista_pasajeros]

        # Validación: Un menor de edad no puede viajar solo
        hay_menor_enviado = any(p.get('es_menor', False) for p in lista_pasajeros)
        hay_adulto_enviado = any(not p.get('es_menor', False) for p in lista_pasajeros)
        hay_adulto_en_carro = ItemCarro.objects.filter(
            carro=carro, servicio=servicio, es_menor=False
        ).exclude(asiento_id__in=asientos_ids_enviados).exists()

        if hay_menor_enviado and not (hay_adulto_enviado or hay_adulto_en_carro):
            return Response(
                {'error': 'Un menor de edad no puede viajar solo. Debe ir acompañado de al menos un pasajero adulto en el mismo servicio.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        for p in lista_pasajeros:
            try:
                validar_rut_chileno(p['rut'])
            except Exception as e:
                return Response({'error': f"RUT {p['rut']} inválido: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

            asiento = get_object_or_404(Asiento, id=p['asiento_id'], bus=servicio.bus)
            if Boleto.objects.filter(servicio=servicio, asiento=asiento, venta__estado__in=['PAGADO', 'ENTREGADO']).exists():
                return Response({'error': f'El asiento #{asiento.numero} ya se encuentra adquirido.'}, status=status.HTTP_400_BAD_REQUEST)

            ItemCarro.objects.update_or_create(
                carro=carro, servicio=servicio, asiento=asiento,
                defaults={
                    'nombre_ocupante': p['nombre'],
                    'apellido_ocupante': p.get('apellido', ''),
                    'rut_ocupante': p['rut'],
                    'es_menor': p.get('es_menor', False)
                }
            )

        return Response(CarroPasajesSerializer(carro).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def delete(self, request):
        carro, _ = CarroPasajes.objects.get_or_create(usuario=request.user)
        carro.items.all().delete()
        return Response({'mensaje': 'Carro vaciado correctamente.'}, status=status.HTTP_204_NO_CONTENT)


class CheckoutAPI(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={201: VentaSerializer})
    @transaction.atomic
    def post(self, request):
        carro = get_object_or_404(CarroPasajes, usuario=request.user)
        items = list(carro.items.select_related('servicio', 'asiento').all())

        if not items:
            return Response({'error': 'El carro de compras está vacío.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validación de menores acompañados por servicio antes de crear la Venta
        servicios_en_carro = set(i.servicio for i in items)
        for srv in servicios_en_carro:
            items_srv = [i for i in items if i.servicio_id == srv.id]
            if any(i.es_menor for i in items_srv) and not any(not i.es_menor for i in items_srv):
                return Response(
                    {'error': f'Un menor de edad no puede viajar solo en el recorrido {srv}. Debe ir acompañado de un adulto.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

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
                apellido_pasajero=item.apellido_ocupante,
                rut_pasajero=item.rut_ocupante,
                es_menor=item.es_menor,
                precio_pagado=item.obtener_subtotal()
            )

        carro.items.all().delete()
        return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)


class PagarVentaAPI(APIView):
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

        list(Asiento.objects.filter(id__in=asientos_ids).select_for_update())

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


class CambiarEstadoVentaAPI(APIView):
    permission_classes = [EsAdminFlota]

    @extend_schema(request=CambiarEstadoSerializer, responses={200: VentaSerializer})
    @transaction.atomic
    def patch(self, request, pk):
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