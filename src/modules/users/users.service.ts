import type { Role } from '@prisma/client';
import { prisma } from '../../lib/prisma';
import { notFound } from '../../lib/errors';

export interface Profile {
  id: number;
  name: string;
  email: string;
  role: Role;
  department: { id: number; name: string } | null;
  manager: { id: number; name: string } | null;
  emailVerified: boolean;
  mustChangePassword: boolean;
}

export async function getProfile(userId: number): Promise<Profile> {
  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: {
      id: true,
      name: true,
      email: true,
      role: true,
      emailVerifiedAt: true,
      mustChangePassword: true,
      department: { select: { id: true, name: true } },
      manager: { select: { id: true, name: true } },
    },
  });
  if (!user) throw notFound();

  const { emailVerifiedAt, ...rest } = user;
  return { ...rest, emailVerified: emailVerifiedAt !== null };
}
