from django import forms

from .models import RemnawaveServer


class RemnawaveNodeCreateForm(forms.Form):

    server = forms.ModelChoiceField(
        label="Сервер Remnawave",
        queryset=RemnawaveServer.objects.none(),
        empty_label=None,
    )

    name = forms.CharField(
        label="Название ноды",
        max_length=100,
        help_text="Например: FI-02, DE-FRA-02, NL-AMS-02",
    )

    address = forms.GenericIPAddressField(
        label="IPv4-адрес",
        protocol="IPv4",
    )

    country_code = forms.CharField(
        label="Страна",
        max_length=2,
        min_length=2,
        help_text="Двухбуквенный код: FI, DE, NL, SE",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["server"].queryset = (
            RemnawaveServer.objects
            .filter(is_active=True)
            .order_by("priority", "id")
        )

    def clean_country_code(self):
        return (
            self.cleaned_data["country_code"]
            .strip()
            .upper()
        )

    def clean_name(self):
        return self.cleaned_data["name"].strip()
