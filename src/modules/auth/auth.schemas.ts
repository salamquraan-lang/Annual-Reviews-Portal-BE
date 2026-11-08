import { z } from 'zod';
import { passwordSchema } from '../../lib/password';

export const emailSchema = z.string().trim().toLowerCase().email('Enter a valid email address.').max(255);

export const signupSchema = z.object({
  name: z.string().trim().min(2, 'Name must be at least 2 characters.').max(120),
  email: emailSchema,
  password: passwordSchema,
});

export const loginSchema = z.object({
  email: emailSchema,
  password: z.string().min(1, 'Password is required.').max(200),
});

export const emailOnlySchema = z.object({ email: emailSchema });

export const tokenSchema = z.object({ token: z.string().trim().min(1, 'Token is required.').max(200) });

export const changePasswordSchema = z
  .object({
    currentPassword: z.string().min(1, 'Current password is required.').max(200),
    newPassword: passwordSchema,
  })
  .refine((d) => d.currentPassword !== d.newPassword, {
    path: ['newPassword'],
    message: 'The new password must be different from the current one.',
  });

export const resetPasswordSchema = z.object({
  token: z.string().trim().min(1, 'Token is required.').max(200),
  newPassword: passwordSchema,
});

export type SignupInput = z.infer<typeof signupSchema>;
export type LoginInput = z.infer<typeof loginSchema>;
export type ChangePasswordInput = z.infer<typeof changePasswordSchema>;
export type ResetPasswordInput = z.infer<typeof resetPasswordSchema>;
