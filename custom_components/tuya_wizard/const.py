DOMAIN = "tuya_wizard"
CONF_TUYA_ACCESS_ID = "tuya_access_id"
CONF_TUYA_ACCESS_SECRET = "tuya_access_secret"
CONF_TUYA_REGION = "tuya_region"
CONF_ONVIF_DEFAULT_PASSWORD = "onvif_default_password"
CARD_STORE_KEY = "tuya_wizard_cards"
CARD_STORE_VERSION = 1
CARDS_WWW_DIR = "/config/www/tuya_wizard_cards"
# Tuya categories that typically expose an ONVIF interface
CAMERA_CATEGORIES: frozenset[str] = frozenset({"sp", "ipc", "vcs"})
