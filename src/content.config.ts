import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const blog = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    published: z.coerce.date(),
    updated: z.coerce.date().optional(),
    draft: z.boolean().default(false),
    category: z.string(),
    tags: z.array(z.string()).default([]),
    featured: z.boolean().default(false),
    author: z.string().default('nabunana'),
    readingTime: z.string().default('6 min'),
  }),
});

const projects = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/projects' }),
  schema: z.object({
    name: z.string(),
    description: z.string(),
    status: z.enum(['Active', 'Shipping', 'Archived']),
    tech: z.array(z.string()),
    featured: z.boolean().default(false),
    url: z.string().optional(),
    article: z.string().optional(),
    repo: z.string().optional(),
    live: z.string().optional(),
  }),
});

const notes = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/notes' }),
  schema: z.object({
    title: z.string(),
    description: z.string().optional(),
    created: z.coerce.date(),
    tags: z.array(z.string()).default([]),
  }),
});

export const collections = { blog, projects, notes };
