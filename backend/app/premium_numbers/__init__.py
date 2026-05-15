def extract_and_store_premium_numbers(*args, **kwargs):
    from app.premium_numbers.service import extract_and_store_premium_numbers as _impl

    return _impl(*args, **kwargs)


__all__ = ["extract_and_store_premium_numbers"]
