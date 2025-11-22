import { Box, Paper, Stack, Typography } from "@mui/material";

interface PlaceholderCardProps {
  title: string;
  subtitle?: string;
  height?: number | string;
  actionText?: string;
}

const PlaceholderCard = ({ title, subtitle, height = 220, actionText }: PlaceholderCardProps) => {
  return (
    <Paper
      elevation={0}
      sx={{
        height,
        p: 2.5,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        border: "1px dashed #E3D4C1",
        background: "linear-gradient(180deg, #FFF8EE 0%, #F6E6D4 100%)"
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="baseline" spacing={1}>
        <Typography variant="subtitle1" fontWeight={700}>
          {title}
        </Typography>
        {actionText && (
          <Typography variant="caption" color="text.secondary">
            {actionText}
          </Typography>
        )}
      </Stack>
      {subtitle && (
        <Typography variant="body2" color="text.secondary">
          {subtitle}
        </Typography>
      )}
      <Box
        sx={{
          flex: 1,
          borderRadius: 2,
          border: "1px dashed #D6C4B2",
          background:
            "repeating-linear-gradient(45deg, #F6E6D4, #F6E6D4 10px, #F0DEC7 10px, #F0DEC7 20px)"
        }}
      />
    </Paper>
  );
};

export default PlaceholderCard;
