from django.contrib import admin

from .models import (
    AIRecommendation,
    Category,
    Currency,
    HouseholdMember,
    NotificationConfig,
    PaymentMethod,
    Subscription,
    SubscriptionPaymentRecord,
    UserSettings,
    WishlistItem,
)

admin.site.register(Currency)
admin.site.register(Category)
admin.site.register(HouseholdMember)
admin.site.register(PaymentMethod)
admin.site.register(UserSettings)
admin.site.register(Subscription)
admin.site.register(SubscriptionPaymentRecord)
admin.site.register(WishlistItem)
admin.site.register(NotificationConfig)
admin.site.register(AIRecommendation)
