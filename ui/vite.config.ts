import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API（mandara serve、既定 127.0.0.1:8765）へは開発サーバーが中継する
export default defineConfig({
  plugins: [react()],
  worker: { format: "es" }, // MapLibre の Worker はモジュールとして動く
  // ローカルで使う道具なので、ライブラリ（Cytoscape、MapLibre）の大きさは許す。地図は開いたときに読む（App.tsx）
  build: { chunkSizeWarningLimit: 1100 },
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
