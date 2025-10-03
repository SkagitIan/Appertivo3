"""Forms used by the internal assets workspace."""

from __future__ import annotations

from django import forms

from .models import AssetFolder, AssetModel, PromptTemplate


class _PinValidationMixin:
    """Provide a reusable validator for four digit PIN codes."""

    def _clean_pin_value(self, value: str) -> str:
        digits = (value or "").strip()
        if not digits.isdigit() or len(digits) != 4:
            raise forms.ValidationError("Enter a 4 digit PIN.")
        return digits


class AssetModelForm(forms.ModelForm):
    """Allow staff to register a Replicate model."""

    class Meta:
        model = AssetModel
        fields = ["description", "identifier"]
        widgets = {
            "description": forms.TextInput(attrs={
                "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                "placeholder": "High fidelity food photography",
            }),
            "identifier": forms.TextInput(attrs={
                "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                "placeholder": "owner/model:version",
            }),
        }


class PromptTemplateForm(forms.ModelForm):
    """Capture reusable prompt text snippets."""

    class Meta:
        model = PromptTemplate
        fields = ["title", "text"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                "placeholder": "Mood board description",
            }),
            "text": forms.Textarea(attrs={
                "rows": 4,
                "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                "placeholder": "Detailed photographic prompt...",
            }),
        }


class AssetGenerationForm(forms.Form):
    """Form used to launch a Replicate image generation."""

    model = forms.ModelChoiceField(
        queryset=AssetModel.objects.none(),
        label="Model",
        widget=forms.Select(attrs={
            "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
        }),
    )
    prompt_template = forms.ModelChoiceField(
        queryset=PromptTemplate.objects.none(),
        required=False,
        label="Prompt library",
        widget=forms.Select(attrs={
            "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
        }),
    )
    prompt_text = forms.CharField(
        label="Prompt",
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 6,
            "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
            "placeholder": "Describe the asset you want to generate...",
        }),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["model"].queryset = AssetModel.objects.all()
        self.fields["prompt_template"].queryset = PromptTemplate.objects.all()


class AssetSaveForm(forms.Form):
    """Persist a generated asset preview to disk."""

    model_id = forms.IntegerField(widget=forms.HiddenInput)
    prompt_text = forms.CharField(widget=forms.HiddenInput)
    preview_url = forms.CharField(widget=forms.HiddenInput)
    storage_path = forms.CharField(required=False, widget=forms.HiddenInput)
    folder_id = forms.ChoiceField(
        required=False,
        choices=(),
        widget=forms.Select(
            attrs={
                "class": "rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400",
            }
        ),
    )

    def clean(self):
        data = super().clean()
        if not data.get("preview_url"):
            raise forms.ValidationError("Preview information is missing.")
        return data

    def clean_folder_id(self) -> int | None:
        value = self.cleaned_data.get("folder_id")
        if not value:
            return None
        try:
            folder_id = int(value)
        except (TypeError, ValueError) as exc:
            raise forms.ValidationError("Choose a valid folder.") from exc
        if not AssetFolder.objects.filter(pk=folder_id).exists():
            raise forms.ValidationError("Choose a valid folder.")
        return folder_id

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        folder_choices = [("", "No folder")]
        folder_choices.extend((str(folder.pk), folder.name) for folder in AssetFolder.objects.all())
        self.fields["folder_id"].choices = folder_choices


class AssetFolderForm(_PinValidationMixin, forms.ModelForm):
    """Create a folder that can group saved assets."""

    class Meta:
        model = AssetFolder
        fields = ["name", "pin", "is_locked"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                    "placeholder": "New folder name",
                }
            ),
            "pin": forms.TextInput(
                attrs={
                    "class": "mt-1 w-28 rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                    "placeholder": "5250",
                    "inputmode": "numeric",
                    "maxlength": "4",
                }
            ),
            "is_locked": forms.CheckboxInput(
                attrs={
                    "class": "h-4 w-4 rounded border-slate-300 text-purple-600 focus:ring-purple-500",
                }
            ),
        }


    def clean_pin(self) -> str:
        return self._clean_pin_value(self.cleaned_data.get("pin", ""))


class AssetFolderSecurityForm(_PinValidationMixin, forms.ModelForm):
    """Update the security settings for an existing folder."""

    class Meta:
        model = AssetFolder
        fields = ["pin", "is_locked"]
        widgets = {
            "pin": forms.TextInput(
                attrs={
                    "class": "mt-1 w-24 rounded-lg border border-slate-200 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400",
                    "inputmode": "numeric",
                    "maxlength": "4",
                }
            ),
            "is_locked": forms.CheckboxInput(
                attrs={
                    "class": "h-4 w-4 rounded border-slate-300 text-purple-600 focus:ring-purple-500",
                }
            ),
        }

    def clean_pin(self) -> str:
        return self._clean_pin_value(self.cleaned_data.get("pin", ""))


class AssetFolderDeleteForm(forms.Form):
    """Validate folder deletion requests."""

    folder_id = forms.IntegerField(widget=forms.HiddenInput)


class AssetFolderAssignmentForm(forms.Form):
    """Assign an asset to a folder or remove the assignment."""

    asset_id = forms.IntegerField(widget=forms.HiddenInput)
    folder_id = forms.IntegerField(required=False)


class AssetDeleteForm(forms.Form):
    """Remove a saved generated asset."""

    asset_id = forms.IntegerField(widget=forms.HiddenInput)
