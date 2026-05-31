"""Pure geospatial helper functions used by the dispatch engine.

Kept dependency-free (no PostGIS / shapely) so the app runs anywhere.
A production deployment would push these computations into PostGIS
(architecture §8 A6), but the maths below is correct for city-scale areas.
"""

from __future__ import annotations

import math

# Mean radius of the Earth in kilometres, used by the haversine formula.
EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance between two points in kilometres."""
    # Convert all coordinates from degrees to radians.
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)

    # Standard haversine formula.
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Test whether a point lies inside a polygon using ray casting.

    `polygon` is a list of [lat, lng] vertices. The algorithm counts how many
    times a horizontal ray from the point crosses the polygon edges; an odd
    count means the point is inside.
    """
    inside = False
    n = len(polygon)
    if n < 3:
        return False

    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygon[i][0], polygon[i][1]
        lat_j, lng_j = polygon[j][0], polygon[j][1]

        # Does the edge (j -> i) straddle the point's latitude, and is the
        # intersection point to the right of the test point's longitude?
        intersects = ((lng_i > lng) != (lng_j > lng)) and (
            lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i
        )
        if intersects:
            inside = not inside
        j = i

    return inside
