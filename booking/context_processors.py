from django.conf import settings


def clinic(request):
    return {"clinic_name": settings.CLINIC_NAME}
