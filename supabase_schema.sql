CREATE TABLE IF NOT EXISTS public.users (
    user_id int8 PRIMARY KEY,
    username text,
    first_name text,
    status text DEFAULT 'pending',
    custom_password text,
    proxy text,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.accounts (
    id int8 PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id int8 REFERENCES public.users(user_id) ON DELETE CASCADE,
    site_id text NOT NULL,
    email text NOT NULL,
    password text NOT NULL,
    invite_code text,
    is_linked bool DEFAULT false,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.gmail_credentials (
    id int8 PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    email text UNIQUE NOT NULL,
    app_password text NOT NULL,
    created_at timestamptz DEFAULT now()
);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gmail_credentials ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all for users" ON public.users;
CREATE POLICY "Allow all for users" ON public.users FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow all for accounts" ON public.accounts;
CREATE POLICY "Allow all for accounts" ON public.accounts FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow all for gmail" ON public.gmail_credentials;
CREATE POLICY "Allow all for gmail" ON public.gmail_credentials FOR ALL USING (true) WITH CHECK (true);
