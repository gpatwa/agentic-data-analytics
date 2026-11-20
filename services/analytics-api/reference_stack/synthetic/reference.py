"""Fixed reference lists. Real city names and IANA time zones, invented restaurant and street words. No coordinates, no real people."""

from __future__ import annotations

# (city, country, IANA time zone, currency). The list order is the popularity rank (Zipf), so the first cities carry the most orders.
CITIES: tuple[tuple[str, str, str, str], ...] = (
    ("New York", "US", "America/New_York", "USD"), ("Los Angeles", "US", "America/Los_Angeles", "USD"), ("Chicago", "US", "America/Chicago", "USD"),
    ("London", "GB", "Europe/London", "GBP"), ("Houston", "US", "America/Chicago", "USD"), ("Phoenix", "US", "America/Phoenix", "USD"),
    ("Philadelphia", "US", "America/New_York", "USD"), ("San Antonio", "US", "America/Chicago", "USD"), ("San Diego", "US", "America/Los_Angeles", "USD"),
    ("Dallas", "US", "America/Chicago", "USD"), ("Berlin", "DE", "Europe/Berlin", "EUR"), ("Paris", "FR", "Europe/Paris", "EUR"),
    ("San Jose", "US", "America/Los_Angeles", "USD"), ("Austin", "US", "America/Chicago", "USD"), ("Jacksonville", "US", "America/New_York", "USD"),
    ("Manchester", "GB", "Europe/London", "GBP"), ("Madrid", "ES", "Europe/Madrid", "EUR"), ("Seattle", "US", "America/Los_Angeles", "USD"),
    ("Denver", "US", "America/Denver", "USD"), ("Boston", "US", "America/New_York", "USD"), ("Rome", "IT", "Europe/Rome", "EUR"),
    ("Nashville", "US", "America/Chicago", "USD"), ("Portland", "US", "America/Los_Angeles", "USD"), ("Munich", "DE", "Europe/Berlin", "EUR"),
    ("Las Vegas", "US", "America/Los_Angeles", "USD"), ("Atlanta", "US", "America/New_York", "USD"), ("Birmingham", "GB", "Europe/London", "GBP"),
    ("Miami", "US", "America/New_York", "USD"), ("Lyon", "FR", "Europe/Paris", "EUR"), ("Barcelona", "ES", "Europe/Madrid", "EUR"),
    ("Minneapolis", "US", "America/Chicago", "USD"), ("Hamburg", "DE", "Europe/Berlin", "EUR"), ("Milan", "IT", "Europe/Rome", "EUR"),
    ("Tampa", "US", "America/New_York", "USD"), ("Leeds", "GB", "Europe/London", "GBP"), ("Cologne", "DE", "Europe/Berlin", "EUR"),
    ("Marseille", "FR", "Europe/Paris", "EUR"), ("Salt Lake City", "US", "America/Denver", "USD"), ("Glasgow", "GB", "Europe/London", "GBP"),
    ("Valencia", "ES", "Europe/Madrid", "EUR"),
)
CUISINES = ("Pizza", "Burgers", "Sushi", "Thai", "Mexican", "Indian", "Italian", "Chinese", "Mediterranean", "Bakery", "Salads", "Breakfast")
SPELLING_VARIANTS = 4  # upper case, trailing space, " Place" suffix, lower case
WORDS_A = ("Golden", "Little", "Blue", "Happy", "Silver", "Rustic", "Urban", "Sunny", "Royal", "Lucky", "Cozy", "Fresh", "Grand", "Quick", "Spicy", "Smoky", "Green", "Red", "Wild", "Old")
WORDS_B = ("Spoon", "Fork", "Table", "Kitchen", "Grill", "Oven", "Garden", "Corner", "Bowl", "Plate", "House", "Cart", "Pot", "Wok", "Bistro", "Cafe", "Diner", "Barn", "Stall", "Deli")
STREETS = ("Alder", "Birch", "Cedar", "Dover", "Elm", "Fairview", "Garnet", "Harbor", "Ivory", "Juniper", "Kestrel", "Linden", "Maple", "Nectar", "Orchard", "Pioneer", "Quarry", "Ridge", "Summit", "Willow")
DISHES = tuple(f"{a} {b}" for a in ("Margherita", "Pepperoni", "Veggie", "Spicy", "Classic", "Double", "Garden", "Smoked", "Crispy", "Sweet") for b in ("Plate", "Bowl", "Wrap"))
PROMOS = {10: "WELCOME10", 15: "SAVE15", 20: "FRIDAY20"}
WEEKDAY_FACTOR = (0.8, 0.8, 0.9, 1.0, 1.3, 1.4, 1.2)  # Monday first
HOLIDAY_MONTH_DAY = ((2, 14), (7, 4), (12, 24), (12, 31))
HOUR_WEIGHT = (1, 1, 0.5, 0.3, 0.3, 0.5, 1, 2, 3, 3, 4, 7, 9, 6, 4, 4, 5, 9, 12, 12, 10, 6, 3, 2)  # local hour of day: lunch and dinner peaks
TEST_RESTAURANT = ("R000000", "TEST KITCHEN")
