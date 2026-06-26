export function formatServerWsAddress(ip: string, port: number): string {
  return `ws://${ip}:${port}`;
}
