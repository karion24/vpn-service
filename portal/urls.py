from .karion_webhook_receiver import receive_payment_event
from portal import payment_checkout
from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView

from . import views
from .forms import LoginForm


urlpatterns = [
    path("api/webhooks/karion-payments/", receive_payment_event, name="karion_payments_webhook"),
    path(
        "",
        views.home,
        name="home",
    ),


    path(
        "how-to-connect/",
        views.how_to_connect,
        name="how_to_connect",
    ),

    path(
        "register/",
        views.register_view,
        name="register",
    ),

    path(
        "login/",
        LoginView.as_view(
            template_name="portal/login.html",
            authentication_form=LoginForm,
        ),
        name="login",
    ),

    path(
        "logout/",
        LogoutView.as_view(),
        name="logout",
    ),

    path(
        "cabinet/",
        views.cabinet,
        name="cabinet",
    ),

    path(
        "cabinet/plans/",
        views.plans_view,
        name="plans",
    ),

    path(
        "cabinet/subscriptions/<int:client_id>/devices/add/",
        views.add_device,
        name="add_device",
    ),

    path(
        "cabinet/devices/<int:device_id>/remove/",
        views.remove_device,
        name="remove_device",
    ),

    path(
        "cabinet/subscriptions/<int:client_id>/renew/",
        views.renew_subscription,
        name="renew_subscription",
    ),

    path(
        "cabinet/orders/create/<int:plan_id>/",
        views.create_order,
        name="create_order",
    ),

    path(
        "cabinet/orders/<int:order_id>/",
        views.order_detail,
        name="order_detail",
    ),

    path(
        "cabinet/orders/<int:order_id>/pay/",
        payment_checkout.pay_order,
        name="pay_order",
    ),

    path(
        "cabinet/orders/<int:order_id>/cancel/",
        views.cancel_order,
        name="cancel_order",
    ),

    path(
        "monitor/",
        views.vpn_monitor,
        name="vpn_monitor",
    ),

    path(
        "connect/",
        views.connect_index,
        name="connect_index",
    ),

    path(
        "device/<str:token>/",
        views.device_connect_client,
        name="device_connect",
    ),

    path(
        "device/<str:token>/full.json",
        views.device_connect_full,
        name="device_connect_full",
    ),

    path(
        "device/<str:token>/selective.json",
        views.device_connect_selective,
        name="device_connect_selective",
    ),

    path(
        "device/<str:token>/windows/",
        views.device_windows_download,
        name="device_windows_download",
    ),

]
