import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// 文字（Barlow / Barlow Condensed。OFL-1.1。ビルドに同梱し、実行時に外部へ読みに行かない）
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-condensed/500.css";
import "@fontsource/barlow-condensed/600.css";
import "./theme.css";

const root = document.getElementById("root");
if (root === null) throw new Error("#root が無い");
createRoot(root).render(<StrictMode><App /></StrictMode>);
