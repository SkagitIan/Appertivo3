import calendar
from collections import defaultdict


def month_cells(year, month, specials):
    """Return calendar weeks for the given month with specials attached."""
    cal = calendar.Calendar()
    weeks = cal.monthdatescalendar(year, month)
    specials_by_day = defaultdict(list)
    for special in specials:
        specials_by_day[special.start_date.date()].append(special)
    result = []
    for week in weeks:
        result.append([
            {
                "date": day,
                "specials": specials_by_day.get(day, []),
                "in_month": day.month == month,
            }
            for day in week
        ])
    return result
