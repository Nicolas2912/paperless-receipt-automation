import { useState } from "react";
import {
  Box,
  Button,
  Paper,
  Stack,
  TextField,
  Typography,
  Chip,
  Divider,
  Avatar
} from "@mui/material";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import { GlobalFilters } from "../components/GlobalFilterBar";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface AIChatViewProps {
  filters: GlobalFilters;
}

const starterMessages: Message[] = [
  {
    id: "1",
    role: "assistant",
    content: "Hi! Ask me about spend trends, top merchants, or data issues. I'll answer using your filters."
  },
  {
    id: "2",
    role: "user",
    content: "Show my top merchants this month and payment-method split."
  },
  {
    id: "3",
    role: "assistant",
    content: "Great. I would query receipts with the current filters, aggregate by merchant, and return a chart-ready list."
  }
];

const AIChatView = ({ filters }: AIChatViewProps) => {
  const [messages, setMessages] = useState<Message[]>(starterMessages);
  const [draft, setDraft] = useState("");

  const handleSend = () => {
    if (!draft.trim()) return;
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", content: draft.trim() },
      {
        id: `a-${Date.now() + 1}`,
        role: "assistant",
        content:
          "Placeholder response. Wire this to your LLM service with filtered context (receipts, merchants, products)."
      }
    ]);
    setDraft("");
  };

  return (
    <Stack spacing={2}>
      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #e4e4e7" }}>
        <Typography variant="h6" fontWeight={800} sx={{ mb: 0.5 }}>
          AI / LLM chat
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Chat with an assistant about your receipts, merchants, and products. Current filters: {filters.timeRange} •{" "}
          {filters.currency} • Merchant {filters.merchantId ? `#${filters.merchantId}` : "All"}.
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Replace the placeholder handler with your LLM API call (pass filters + recent rows for grounding).
        </Typography>
      </Paper>

      <Paper
        elevation={0}
        sx={{
          border: "1px solid #e4e4e7",
          display: "flex",
          flexDirection: "column",
          minHeight: 460
        }}
      >
        <Box sx={{ p: 2, display: "flex", gap: 1, alignItems: "center" }}>
          <Chip icon={<SmartToyIcon />} label="LLM ready" color="secondary" variant="outlined" />
          <Chip label="Context: filters + top rows" variant="outlined" />
          <Chip label="Output: text + chart hints" variant="outlined" />
        </Box>
        <Divider />

        <Stack spacing={2} sx={{ p: 2, flex: 1, overflowY: "auto" }}>
          {messages.map((msg) => (
            <Stack key={msg.id} direction="row" spacing={1.5} alignItems="flex-start">
              <Avatar
                sx={{
                  bgcolor: msg.role === "assistant" ? "#2563eb" : "#f59e0b",
                  color: "#ffffff",
                  width: 32,
                  height: 32
                }}
              >
                {msg.role === "assistant" ? <SmartToyIcon fontSize="small" /> : <ChatBubbleOutlineIcon fontSize="small" />}
              </Avatar>
              <Paper
                variant="outlined"
                sx={{
                  p: 1.5,
                  backgroundColor: msg.role === "assistant" ? "#f8fafc" : "#ffffff",
                  borderColor: "#e4e4e7",
                  maxWidth: "80%"
                }}
              >
                <Typography variant="body2">{msg.content}</Typography>
              </Paper>
            </Stack>
          ))}
        </Stack>

        <Divider />
        <Box sx={{ p: 2, display: "flex", gap: 1 }}>
          <TextField
            fullWidth
            placeholder="Ask about spend trends, anomalies, merchant comparisons..."
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            size="small"
            multiline
            minRows={2}
          />
          <Button variant="contained" onClick={handleSend} sx={{ alignSelf: "stretch", minWidth: 120 }}>
            Send
          </Button>
        </Box>
      </Paper>
    </Stack>
  );
};

export default AIChatView;
