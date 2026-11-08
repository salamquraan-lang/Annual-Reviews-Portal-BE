import fs from 'node:fs/promises';
import path from 'node:path';
import nodemailer from 'nodemailer';
import { env } from '../config/env';

export interface Mail {
  to: string;
  subject: string;
  text: string;
  html?: string;
}

export interface Mailer {
  send(mail: Mail): Promise<void>;
}

// Used when SMTP_HOST is empty: writes each email to tmp/mail/ instead of sending it.
class FileMailer implements Mailer {
  private readonly dir = path.resolve(__dirname, '../../tmp/mail');

  async send(mail: Mail): Promise<void> {
    await fs.mkdir(this.dir, { recursive: true });
    const safeTo = mail.to.replace(/[^a-z0-9@._-]/gi, '_');
    const file = path.join(this.dir, `${Date.now()}-${safeTo}.txt`);
    await fs.writeFile(file, `To: ${mail.to}\nSubject: ${mail.subject}\n\n${mail.text}\n`, 'utf8');
    console.log(`[mail] written to ${file}`);
  }
}

class SmtpMailer implements Mailer {
  private readonly transport = nodemailer.createTransport({
    host: env.SMTP_HOST,
    port: env.SMTP_PORT,
    secure: env.SMTP_PORT === 465,
    auth: env.SMTP_USER ? { user: env.SMTP_USER, pass: env.SMTP_PASS } : undefined,
  });

  async send(mail: Mail): Promise<void> {
    await this.transport.sendMail({ from: env.MAIL_FROM, ...mail });
  }
}

let current: Mailer = env.SMTP_HOST ? new SmtpMailer() : new FileMailer();

export const mailer: Mailer = {
  send: (mail) => current.send(mail),
};

export function setMailer(m: Mailer): void {
  current = m;
}
