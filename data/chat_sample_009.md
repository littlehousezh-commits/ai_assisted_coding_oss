<!-- SpecStory chat sample; session-identifying header metadata omitted. -->

# chat_sample_009

_**User**_

增加一个博客顺序属性

---

_**Assistant**_

```typescript:src/content.config.ts
// ... existing code ...
const blog = defineCollection({
	// Load Markdown and MDX files in the `src/content/blog/` directory.
	loader: glob({ base: './src/content/blog', pattern: '**/*.{md,mdx}' }),
	// Type-check frontmatter using a schema
	schema: z.object({
		title: z.string(),
		description: z.string(),
		// Transform string to Date object
		pubDate: z.coerce.date(),
		updatedDate: z.coerce.date().optional(),
		heroImage: z.string().optional(),
		onTop: z.boolean().optional(),
		series: z.string().optional(),
		order: z.number().optional(), // 新增博客顺序属性
	}),
});
// ... existing code ...
```

在博客的 schema 中新增了一个 `order` 属性，类型为 `number`，并且是可选的。这个属性可以用来控制博客的显示顺序。

---

_**User**_

子元素flex排列，用" ,"分隔

---

_**Assistant**_

```language:src/pages/blog/index.astro
// ... existing code ...
									<div class="info" style="display: flex; gap: 0.5rem; align-items: center;">
										<p class="date">
											<FormattedDate date={post.data.pubDate} />
										</p>
										<p class="series">
											{post.data.series}
										</p>
									</div>
// ... existing code ...
```

在 `.info` 元素上添加了 `display: flex` 样式，使子元素水平排列，并添加了 `gap: 0.5rem` 来设置元素之间的间距，`align-items: center` 使子元素垂直居中对齐。

---

_**User**_

增加order属性，按由小到大排序

---

_**Assistant**_

```language=astro:src/pages/blog/index.astro
// ... existing code ...
const posts = (await getCollection('blog')).sort(
	(a, b) => {
		if (a.data.onTop && !b.data.onTop) return -1;
		if (!a.data.onTop && b.data.onTop) return 1;
		if (a.data.order && b.data.order) return a.data.order - b.data.order; // 新增按order排序
		return b.data.pubDate.valueOf() - a.data.pubDate.valueOf();
	},
);
// ... existing code ...
```

在排序逻辑中新增了按 `order` 属性排序的规则，如果两个博客都有 `order` 属性，则按 `order` 由小到大排序。如果 `order` 不存在，则保持原有的按发布时间排序的规则。

---

_**User**_

有order属性的排在前

---

_**Assistant**_

```language=language=astro:src/pages/blog/index.astro
// ... existing code ...
const posts = (await getCollection('blog')).sort(
	(a, b) => {
		if (a.data.onTop && !b.data.onTop) return -1;
		if (!a.data.onTop && b.data.onTop) return 1;
		if (a.data.order && !b.data.order) return -1; // 有order的排在前
		if (!a.data.order && b.data.order) return 1; // 有order的排在前
		if (a.data.order && b.data.order) return a.data.order - b.data.order;
		return b.data.pubDate.valueOf() - a.data.pubDate.valueOf();
	},
);
// ... existing code ...
```

在排序逻辑中新增了两条规则：如果 `a` 有 `order` 而 `b` 没有，则 `a` 排在前；如果 `b` 有 `order` 而 `a` 没有，则 `b` 排在前。这样确保了有 `order` 属性的博客会优先显示。

---

_**User**_

有onTop属性的置顶

---

_**Assistant**_

```language=language=language=astro:src/pages/blog/index.astro
// ... existing code ...
const posts = (await getCollection('blog')).sort(
	(a, b) => {
		if (a.data.onTop && !b.data.onTop) return -1; // 有onTop的置顶
		if (!a.data.onTop && b.data.onTop) return 1; // 有onTop的置顶
		if (a.data.order && !b.data.order) return -1;
		if (!a.data.order && b.data.order) return 1;
		if (a.data.order && b.data.order) return a.data.order - b.data.order;
		return b.data.pubDate.valueOf() - a.data.pubDate.valueOf();
	},
);
// ... existing code ...
```

在排序逻辑中已经包含了 `onTop` 属性的处理规则：如果 `a` 有 `onTop` 而 `b` 没有，则 `a` 排在最前面；如果 `b` 有 `onTop` 而 `a` 没有，则 `b` 排在最前面。这样确保了有 `onTop` 属性的博客会置顶显示。
