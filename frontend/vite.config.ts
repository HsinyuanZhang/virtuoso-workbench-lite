import { defineConfig } from "vitest/config";

export default defineConfig({
  build: {
    outDir: "../src/virtuoso_workbench/static",
    emptyOutDir: true,
    rollupOptions: {
      output: { entryFileNames: "workbench.js", assetFileNames: "workbench[extname]" },
    },
  },
  test: { include: ["src/**/*.test.ts"] },
});
