import Link from "next/link";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center bg-neutral-950 px-4 text-center text-neutral-100">
      <h1 className="text-2xl font-semibold">3lay</h1>
      <p className="mt-2 max-w-sm text-sm text-neutral-400">
        Any input in, structured data out &mdash; via agentic extraction and webhooks.
      </p>
      <Link
        href="/login"
        className="mt-6 rounded-md bg-neutral-100 px-4 py-2 text-sm font-medium text-neutral-900 hover:bg-white"
      >
        Sign in
      </Link>
    </main>
  );
}
