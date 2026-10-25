import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fido — A little company. A lot more focus.",
  description: "Meet your focus companion. Put your phone down, settle into deep work, and let Fido keep you on track.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
