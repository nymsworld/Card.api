// kantomisplay.cc/updates: one of the shop website's own pages, drawn by home.html from the address (lib/home.js)
import { serveHome } from './lib/home.js';

export const onRequest = serveHome;
