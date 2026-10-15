import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API（mandara serve、既定 127.0.0.1:8765）へは開発サーバーが中継する
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
