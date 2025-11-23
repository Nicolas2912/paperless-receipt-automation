import dayjs from "dayjs";

export const resolveDateRange = (timeRange: string): { from: string | null; to: string | null } => {
  const today = dayjs();
  switch (timeRange) {
    case "this_month":
      return { from: today.startOf("month").format("YYYY-MM-DD"), to: today.endOf("month").format("YYYY-MM-DD") };
    case "this_year":
      return { from: today.startOf("year").format("YYYY-MM-DD"), to: today.endOf("month").format("YYYY-MM-DD") };
    case "last_12_months":
      return {
        from: today.startOf("month").subtract(11, "month").format("YYYY-MM-DD"),
        to: today.endOf("month").format("YYYY-MM-DD")
      };
    default:
      return { from: null, to: null };
  }
};
