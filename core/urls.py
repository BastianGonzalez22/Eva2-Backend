"""
Configuración de URLs principales de AndesSur.
"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

from transporte import views, api_views

urlpatterns = [
    # -------------------------------------------------------------------------
    # VISTAS WEB
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
    
    path('gestion/', views.gestion_servicios, name='gestion_servicios'),

    # -------------------------------------------------------------------------
    # API REST
    # -------------------------------------------------------------------------
    path('api/token/', api_views.CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    path('api/servicios/', api_views.BuscarServiciosAPI.as_view(), name='api_servicios'),
    path('api/servicios/<int:servicio_id>/asientos/', api_views.AsientosServicioAPI.as_view(), name='api_asientos_servicio'),
    
    path('api/carro-pasajes/', api_views.CarroPasajesAPI.as_view(), name='api_carro'),
    path('api/carro-pasajes/checkout/', api_views.CheckoutAPI.as_view(), name='api_checkout'),
    path('api/ventas/<int:pk>/pagar/', api_views.PagarVentaAPI.as_view(), name='api_pagar_venta'),
    path('api/ventas/<int:pk>/cancelar/', api_views.CancelarVentaAPI.as_view(), name='api_cancelar_venta'),
    path('api/ventas/<int:pk>/estado/', api_views.CambiarEstadoVentaAPI.as_view(), name='api_cambiar_estado'),
    
    path('api/mis-boletos/', api_views.MisBoletosAPI.as_view(), name='api_mis_boletos'),
    path('api/gestion/servicios/', api_views.GestionServiciosAPI.as_view(), name='api_gestion_servicios'),
    path('api/gestion/servicios/<int:pk>/', api_views.GestionServiciosDetalleAPI.as_view(), name='api_gestion_servicios_detalle'),

    # -------------------------------------------------------------------------
    # DOCUMENTACIÓN SWAGGER / OPENAPI
    # -------------------------------------------------------------------------
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

handler404 = 'transporte.views.error_404_view'