import { useEffect, useState } from 'react';
import { desk, onDeskChange } from '../lib/api';

/** Hosted team site: is the trading PC connected, and how fresh is the saved copy. Always online locally. */
export function useDesk() {
  const [d, setD] = useState(desk);
  useEffect(() => onDeskChange(() => setD(desk)), []);
  return d;
}
