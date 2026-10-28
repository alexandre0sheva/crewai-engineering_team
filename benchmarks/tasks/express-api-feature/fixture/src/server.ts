import { createApp } from "./app.ts";
import { BookStore } from "./store.ts";

const port = Number(process.env.PORT ?? 3000);
const store = new BookStore([
  { title: "The Left Hand of Darkness", author: "Ursula K. Le Guin" },
  { title: "Kindred", author: "Octavia E. Butler" },
]);
createApp(store).listen(port, () => {
  console.log(`library-api listening on http://127.0.0.1:${port}`);
});
