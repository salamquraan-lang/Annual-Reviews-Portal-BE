import bcrypt from 'bcryptjs';
import { z } from 'zod';

const ROUNDS = 12;

// Compared against when the email is unknown, so the response time doesn't reveal it.
const DUMMY_HASH = bcrypt.hashSync('dummy-password-for-timing', ROUNDS);

export function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, ROUNDS);
}

export function verifyPassword(plain: string, hash: string | null | undefined): Promise<boolean> {
  return bcrypt.compare(plain, hash ?? DUMMY_HASH);
}

export const passwordSchema = z
  .string()
  .min(8, 'Password must be at least 8 characters.')
  .max(72, 'Password must be at most 72 characters.')
  .regex(/[A-Za-z]/, 'Password must contain a letter.')
  .regex(/[0-9]/, 'Password must contain a digit.');
