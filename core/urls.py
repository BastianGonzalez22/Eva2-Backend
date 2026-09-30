"""
==============================================================================
CONFIGURACIÓN DE ENRUTAMIENTO PRINCIPAL (core/urls.py)
------------------------------------------------------------------------------
Matriz de endpoints RESTful conforme a la pauta de evaluación:
- Rutas públicas: catálogo, búsqueda con filtros, Swagger/OpenAPI.
- Rutas de pasajero autenticado: carro de compras, checkout, pago, boletos e historial.
- Rutas de administrador: gestión de flota, CRUD de recorridos y estados de venta.
==============================================================================
"""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

from transporte import views, api_views

urlpatterns = [
    # -------------------------------------------------------------------------
    # 1. VISTAS WEB (FRONTEND SSR)
    # -------------------------------------------------------------------------
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('registro/', views.registro_view, name='registro'),
    
    path('servicio/<int:servicio_id>/', views.detalle_servicio, name='detalle_servicio'),
    path('carro/', views.ver_carro, name='ver_carro'),
    path('carro/eliminar/<int:item_id>/', views.eliminar_item_carro, name='eliminar_item_carro'),
    path('carro/checkout/', views.procesar_checkout, name='procesar_checkout'),
    path('venta/<int:venta_id>/pagar/', views.pagar_orden_web, name='pagar_orden_web'),
    path('venta/<int:venta_id>/cancelar/', views.cancelar_orden_web, name='cancelar_orden_web'),
    path('comprobante/<int:boleto_id>/', views.comprobante, name='comprobante'),
    path('mis-compras/', views.mis_compras_view, name='mis_compras'),
    
    path('gestion/', views.gestion_servicios, name='gestion_servicios'),

    # -------------------------------------------------------------------------
    # 2. AUTENTICACIÓN JWT (RBAC)
    # -------------------------------------------------------------------------
    path('api/token/', api_views.CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # -------------------------------------------------------------------------
    # 3. ENDPOINTS DE RECORRIDOS (MATRIZ PAUTA)
    # -------------------------------------------------------------------------
    path('api/servicios/buscar/', api_views.BuscarServiciosAPI.as_view(), name='api_servicios_buscar'),
    path('api/servicios/<int:servicio_id>/asientos/', api_views.AsientosServicioAPI.as_view(), name='api_asientos_servicio'),
    path('api/servicios/', api_views.GestionServiciosAPI.as_view(), name='api_servicios_crud'),
    path('api/servicios/<int:pk>/', api_views.GestionServiciosDetalleAPI.as_view(), name='api_servicios_crud_detalle'),

    # -------------------------------------------------------------------------
    # 4. CARRO DE COMPRAS, CHECKOUT Y TRANSICIÓN DE VENTAS (MATRIZ PAUTA)
    # -------------------------------------------------------------------------
    path('api/carro-pasajes/', api_views.CarroPasajesAPI.as_view(), name='api_carro'),
    path('api/ventas/checkout/', api_views.CheckoutAPI.as_view(), name='api_ventas_checkout'),
    path('api/carro-pasajes/checkout/', api_views.CheckoutAPI.as_view(), name='api_carro_checkout'),
    
    path('api/ventas/<int:pk>/pagar/', api_views.PagarVentaAPI.as_view(), name='api_pagar_venta'),
    path('api/ventas/<int:pk>/cancelar/', api_views.CancelarVentaAPI.as_view(), name='api_cancelar_venta'),
    path('api/ventas/<int:pk>/estado/', api_views.CambiarEstadoVentaAPI.as_view(), name='api_cambiar_estado'),
    
    path('api/mis-boletos/', api_views.MisBoletosAPI.as_view(), name='api_mis_boletos'),

    # -------------------------------------------------------------------------
    # 5. DOCUMENTACIÓN OPENAPI 3.0 / SWAGGER
    # -------------------------------------------------------------------------
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

handler404 = 'transporte.views.error_404_view'