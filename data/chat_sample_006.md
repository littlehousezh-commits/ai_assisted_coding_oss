<!-- SpecStory chat sample; session-identifying header metadata omitted. -->

# chat_sample_006

_**User**_

Generate props for HeroBanner based on the content models fields

---

_**Assistant**_

```diff
- export const HeroBanner = () => {
-   return <div>Hero Banner</div>
+ import React from 'react'
+ 
+ export interface HeroBannerProps {
+   title: string
+   subtitle: string
+   backgroundImage: string
+   ctaText: string
+   ctaLink: string
+   textColor: string
+   backgroundColor: string
+ }
+ 
+ export const HeroBanner = ({
+   title,
+   subtitle,
+   backgroundImage,
+   ctaText,
+   ctaLink,
+   textColor,
+   backgroundColor,
+ }: HeroBannerProps) => {
+   return (
+     <section
+       className='hero-banner'
+       style={{
+         backgroundColor,
+         backgroundImage: `url(${backgroundImage})`,
+         backgroundSize: 'cover',
+         backgroundPosition: 'center',
+       }}
+     >
+       <div className='container mx-auto px-4 py-8'>
+         <h1 style={{color: textColor}} className='text-4xl font-bold mb-2'>
+           {title}
+         </h1>
+         <h2 style={{color: textColor}} className='text-2xl mb-4'>
+           {subtitle}
+         </h2>
+         <a
+           href={ctaLink}
+           className='inline-block bg-white text-black px-4 py-2 rounded hover:bg-gray-200 transition-colors duration-300'
+         >
+           {ctaText}
+         </a>
+       </div>
+     </section>
+   )
  }
  
```
Below is the updated HeroBanner component with props generated from the content model fields:

```typescript:src/components/contentful/blocks/HeroBanner.tsx
import React from 'react'

export interface HeroBannerProps {
  title: string
  subtitle: string
  backgroundImage: string
  ctaText: string
  ctaLink: string
  textColor: string
  backgroundColor: string
}

export const HeroBanner = ({
  title,
  subtitle,
  backgroundImage,
  ctaText,
  ctaLink,
  textColor,
  backgroundColor,
}: HeroBannerProps) => {
  return (
    <section
      className="hero-banner"
      style={{
        backgroundColor,
        backgroundImage: `url(${backgroundImage})`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      <div className="container mx-auto px-4 py-8">
        <h1 style={{ color: textColor }} className="text-4xl font-bold mb-2">
          {title}
        </h1>
        <h2 style={{ color: textColor }} className="text-2xl mb-4">
          {subtitle}
        </h2>
        <a
          href={ctaLink}
          className="inline-block bg-white text-black px-4 py-2 rounded hover:bg-gray-200 transition-colors duration-300"
        >
          {ctaText}
        </a>
      </div>
    </section>
  )
}
```

This implementation directly maps the fields for the HeroBanner content model to the component props. Let me know if you need any adjustments.

---

_**User**_

update HeroBanner to use CtaLinks like so

{
      "id": "ctaLinks",
      "name": "CTA Links",
      "type": "Array",
      "localized": false,
      "required": false,
      "validations": [],
      "disabled": false,
      "omitted": false,
      "items": {
        "type": "Link",
        "validations": [
          {
            "linkContentType": [
              "ctaLink"
            ]
          }
        ],
        "linkType": "Entry"
      }
    }

---

_**Assistant**_

```diff
  import React from 'react'
+ 
+ export interface CTALink {
+   title: string
+   link: string
+ }
  
  export interface HeroBannerProps {
    title: string
    subtitle: string
    backgroundImage: string
-   ctaText: string
-   ctaLink: string
+   ctaLinks?: CTALink[]
    textColor: string
    backgroundColor: string
```

```diff
    subtitle,
    backgroundImage,
-   ctaText,
-   ctaLink,
+   ctaLinks,
    textColor,
    backgroundColor,
```

```diff
            {subtitle}
          </h2>
+         {ctaLinks && ctaLinks.length > 0 && (
+           <div className='cta-links flex gap-4'>
+             {ctaLinks.map((cta, idx) => (
                <a
-           href={ctaLink}
+                 key={idx}
+                 href={cta.link}
                  className='inline-block bg-white text-black px-4 py-2 rounded hover:bg-gray-200 transition-colors duration-300'
                >
-           {ctaText}
+                 {cta.title}
                </a>
+             ))}
+           </div>
+         )}
        </div>
      </section>
```
```diff
  
  export interface CTALink {
+   id: string
    title: string
    link: string
```

```diff
          {ctaLinks && ctaLinks.length > 0 && (
            <div className='cta-links flex gap-4'>
-             {ctaLinks.map((cta, idx) => (
+             {ctaLinks.map((cta) => (
                <a
-                 key={idx}
+                 key={cta.id}
                  href={cta.link}
                  className='inline-block bg-white text-black px-4 py-2 rounded hover:bg-gray-200 transition-colors duration-300'
```
Below is the updated HeroBanner component using ctaLinks with an id for each CTA, which fixes the linter error by using cta.id as the key:

```typescript:src/components/contentful/blocks/HeroBanner.tsx
import React from 'react'

export interface CTALink {
  id: string
  title: string
  link: string
}

export interface HeroBannerProps {
  title: string
  subtitle: string
  backgroundImage: string
  ctaLinks?: CTALink[]
  textColor: string
  backgroundColor: string
}

export const HeroBanner = ({
  title,
  subtitle,
  backgroundImage,
  ctaLinks,
  textColor,
  backgroundColor,
}: HeroBannerProps) => {
  return (
    <section
      className="hero-banner"
      style={{
        backgroundColor,
        backgroundImage: `url(${backgroundImage})`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      <div className="container mx-auto px-4 py-8">
        <h1 style={{ color: textColor }} className="text-4xl font-bold mb-2">
          {title}
        </h1>
        <h2 style={{ color: textColor }} className="text-2xl mb-4">
          {subtitle}
        </h2>
        {ctaLinks && ctaLinks.length > 0 && (
          <div className="cta-links flex gap-4">
            {ctaLinks.map((cta) => (
              <a
                key={cta.id}
                href={cta.link}
                className="inline-block bg-white text-black px-4 py-2 rounded hover:bg-gray-200 transition-colors duration-300"
              >
                {cta.title}
              </a>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
```

This implementation now uses an array of CTA links from the content model, each with its own id, title, and link, and renders them accordingly.

---

_**User**_

@Contentful Introspection find the homepage query

---

_**Assistant**_

---
