export const formatCurrency = (cents: number | null | undefined, currency = "EUR") => {
  if (cents === null || cents === undefined) {
    return "–";
  }
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: 2
  }).format(cents / 100);
};

export const formatDateTime = (iso: string | null | undefined) => {
  if (!iso) {
    return "–";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
};

export const formatPercent = (value: number | null | undefined) => {
  if (value === null || value === undefined) {
    return "–";
  }
  return `${(value * 100).toFixed(0)}%`;
};

export const humaniseKey = (key: string) =>
  key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
