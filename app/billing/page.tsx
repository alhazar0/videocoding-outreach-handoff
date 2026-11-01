import Link from "next/link";
import { BillingCenter } from "../billing-center";
export default function BillingPage() {
  return <main className="billing-standalone"><Link className="billing-back" href="/">← 返回运营控制台</Link><BillingCenter /></main>;
}
