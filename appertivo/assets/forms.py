"""Forms used by the internal assets workspace."""

from __future__ import annotations

from django import forms

from .models import AssetFolder, AssetModel, PromptTemplate


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

    def clean(self):
        data = super().clean()
        if not data.get("preview_url"):
            raise forms.ValidationError("Preview information is missing.")
        return data


class AssetFolderForm(forms.ModelForm):
    """Create a folder that can group saved assets."""

    class Meta:
        model = AssetFolder
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400",
                    "placeholder": "New folder name",
                }
            )
        }


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
