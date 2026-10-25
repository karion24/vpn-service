from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from .models import CustomerProfile


User = get_user_model()


class RegisterForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "name@example.com",
                "autocomplete": "email",
            }
        ),
    )

    password1 = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Пароль",
                "autocomplete": "new-password",
            }
        ),
    )

    password2 = forms.CharField(
        label="Повторите пароль",
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Повторите пароль",
                "autocomplete": "new-password",
            }
        ),
    )

    phone = forms.CharField(
        label="Телефон",
        required=False,
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "placeholder": "+7 999 123-45-67",
                "autocomplete": "tel",
            }
        ),
    )

    telegram = forms.CharField(
        label="Telegram",
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "placeholder": "@username",
            }
        ),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(
            email__iexact=email
        ).exists():
            raise forms.ValidationError(
                "Пользователь с таким Email уже зарегистрирован."
            )

        return email

    def clean(self):
        cleaned = super().clean()

        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")

        if (
            password1
            and password2
            and password1 != password2
        ):
            self.add_error(
                "password2",
                "Пароли не совпадают.",
            )

        return cleaned

    def save(self):
        email = self.cleaned_data["email"]

        # На первом этапе username = email.
        # Клиенту username нигде показывать не будем.
        user = User.objects.create_user(
            username=email,
            email=email,
            password=self.cleaned_data["password1"],
        )

        CustomerProfile.objects.create(
            user=user,
            phone=self.cleaned_data.get(
                "phone",
                "",
            ),
            telegram=self.cleaned_data.get(
                "telegram",
                "",
            ),
        )

        return user


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "name@example.com",
                "autocomplete": "email",
            }
        ),
    )

    password = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Пароль",
                "autocomplete": "current-password",
            }
        ),
    )
