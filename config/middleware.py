from django.conf import settings


def frame_ancestors(get_response):
    """Allow the configured origins to embed the site, replacing X-Frame-Options."""

    def middleware(request):
        response = get_response(request)
        response.headers.pop("X-Frame-Options", None)
        response.headers["Content-Security-Policy"] = (
            f"frame-ancestors {settings.CONTENT_SECURITY_POLICY_FRAME_ANCESTORS}"
        )
        return response

    return middleware
