# Git Commit Signoff and Signing

**NOTE**: "sign-off" is different from "signing" a commit.  The former
indicates your assent to the repository's terms for contributors, the
latter adds a cryptographic signature that is rarely displayed.  See
[the git
book](https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work)
about signing. For commit signoff, do a web search on `git
signoff`. GitHub has a concept of [a commit being
"verified"](https://docs.github.com/en/authentication/managing-commit-signature-verification)
that extends the Git concept of signing.

In order to get a pull request approved, you must first complete a DCO
sign-off for each commit that the request is asking to add to the
repository. This process is defined by the CNCF, and there are two
cases: individual contributors and contributors that work for a
corporate CNCF member. Both mean consent with the terms stated in [the
`DCO` file at the root of this Git
repository](https://github.com/llm-d/llm-d/blob/main/DCO). In
the case of an individual, DCO sign-off is accomplished by doing a Git
"sign-off" on the commit.

We prefer that commits contributed to this repository be signed and
GitHub verified, but this is not strictly necessary or enforced.

## Commit Sign-off

Your submitted PR must pass the automated checks in order to be merged. One of these checks that each commit that you propose to contribute is signed-off. If you use the `git` shell command, this involves passing the `-s` flag on the command line. For example, the following command will create a signed-off commit but _not_ sign it.

```shell
git commit -s
```

Alternatively, the following command will create a commit that is both signed-off and signed.

```shell
git commit -s -S
```

For other tools, consult their documentation.

## Signing Commits

Before signing any commits, you must have a GPG or SSH key. Basic setup instructions can be found below (For more detailed instructions, refer to the Github [GPG](https://docs.github.com/en/authentication/managing-commit-signature-verification/generating-a-new-gpg-key) and [SSH](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent#generating-a-new-ssh-key) setup pages.)

To sign a particular commit, you must either include `-S` on the `git commit` command line (see the command exhibited above for an example) or have configured automatic signing (see ["Everyone Must Sign" in the Git Book](https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work#_everyone_must_sign) for a hint about that).

Before starting, make sure that your user email is verified on Github. To check for this:

1. Login to Github and navigate to your Github **Settings** page
2. In the sidebar, open the **Emails** tab
3. Emails associated with Github should be listed at the top of the page under the "Emails" label
4. An unverified email would have an "Unverified" label under it in orange text
5. To verify, click **Resend verification email** and follow its prompts
6. Navigate back to your **Emails** page, if the "Unverified" label is no longer there, then you're good to go!

<br />

For Windows users, **Git Bash** is also highly recommended.

<br />

## Setting up the GPG Key

1. Install GnuPG (the GPG command line tool).
    - Binary releases for your specific OS can be found [here](https://www.gnupg.org/download/) after scrolling down to the Binary Releases section (i.e. Gpg4win on Windows, Mac GPG for MacOS, etc).
    - After downloading the installer, follow the prompts to set up GnuPG.

2. Open Git Bash (or your CLI of choice) and use the following command to generate your GPG key pair:

   ```shell
   gpg --full-generate-key
   ```

3. If prompted to specify the size, type, and duration of the key that you want, press `Enter` to select the default option.
4. Once prompted, enter your user info and a passphrase:
    - Make sure to list your email as the same one that's verified by Github
5. Use the following command to list the long form of your generated GPG keys:

   ```shell
   gpg --list-secret-keys --keyid-format=long
   ```

    - Your GPG key ID should be the characters on the output line starting with `sec`, beginning directly after the `/` and ending before the listed date.
    - For example, in the output below (from the Github [GPG](https://docs.github.com/en/authentication/managing-commit-signature-verification/generating-a-new-gpg-key) setup page), the GPG key ID would be `3AA5C34371567BD2`

        ```shell
        $ gpg --list-secret-keys --keyid-format=long
         /Users/hubot/.gnupg/secring.gpg
         ------------------------------------
         sec   4096R/3AA5C34371567BD2 2016-03-10 [expires: 2017-03-10]
         uid                          Hubot <hubot@example.com>
         ssb   4096R/4BB6D45482678BE3 2016-03-10
        ```
6. Copy your GPG key ID and run the command below, replacing `[your_GPG_key_ID]` with the key ID you just copied:

    ```shell
    gpg --armor --export [your_GPG_key_ID]
    ```

7. This should generate an output with your GPG key. Copy the characters starting from `-----BEGIN PGP PUBLIC KEY BLOCK-----` and ending at `--END PGP PUBLIC KEY BLOCK-----` (inclusive) to your clipboard.
8. After copying or saving your GPG key, navigate to **Settings** in your Github
9. Navigate to the **SSH and GPG keys** page under the Access section in the sidebar
10. Under GPG keys, select **New GPG key**
    - Enter a suitable name for your key under "Title" and paste your GPG key that you copied/saved in **Step 7** under "Key".
    - Once done, click **Add GPG key**
11. Your new GPG key should now be displayed under GPG keys.

<br />

## Setting up the SSH Key

1. Open Git Bash (or your CLI of choice) and use the following command to generate your new SSH key (make sure to replace `your_email` with your Github-verified email address):

    ```shell
    ssh-keygen -t ed25519 -C "your_email"
    ```
   
2. Press `Enter` to select the default option if prompted to set a save-file or passphrase for the key (you may choose to enter a passphrase if desired; this will prompt you to enter the passphrase every time you perform a DCO sign-off).
   - The following output should generate a `randomart` image 
3. Use the following command to copy the **public** part of the new SSH key to your clipboard:

    ```shell
    clip < ~/.ssh/id_ed25519.pub
    ```

    Note: If you are in a WSL shell, use instead

   ```shell
   clip.exe < ~/.ssh/id_ed25519.pub
   ```

5. After copying or saving your SSH key, navigate to **Settings** in your Github.
6. Navigate to the **SSH and GPG keys** page under the Access section in the sidebar.
7. Under SSH keys, select **New SSH key**.
    - Enter a suitable name for your key under "Title" (it'll pick up the email address if left empty)
    - Open the dropdown menu under "Key type" and select **Signing Key**
    - Paste your SSH public key that you copied/saved in **Step 3** under "Key"
8. Your new SSH key should now be displayed under SSH keys, in the **Signing Key** section.
9. **Optional**: If you want to use the same SSH or GPG key for authentication as well, repeat steps above, selecting **Authentication** as the "Key type".
10. **Optional**: To test if your SSH key is connecting properly or not, run the following command in your CLI (more specific instructions can be found in the [Github documentation](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection)):

    ```shell
    ssh -T git@github.com
    ```

    - If given a warning saying something like `The authenticity of the host '[host IP]' can't be established` along with a key fingerprint and a prompt to continue, verify if the provided key fingerprint matches any of those listed [here](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
    - Once you've verified the match, type `yes`
    - If the resulting message says something along the lines of `Hi [User]! You've successfully authenticated, but GitHub does not provide shell access.`, then it means your SSH key is up and ready.
    - If you get an error saying something like `Error: Permission denied (publickey)` repeat the procedure in step 6 _with the same key_ only select **Authentication Key**. Then try the test command again.

<br />

## Creating Pull Requests Using the GitHub Website

This is not recommended for individual contributors, because the commits that it produces are not "signed-off" (as defined by Git) and thus do not carry assent to the DCO; see [Repairing commits](#repairing-commits) below for a way to recover if you have inadvertently made such a PR. For corporate contributors the DCO assent is indicated differently.

Whether it's editing files from llm-d.ai or directly from the llm-d Github, there are a couple steps to follow that streamlines the workflow of your PR:

1. Changes made to any file are automatically committed to a new branch in your fork.
    - After clicking **Commit changes...**, write your commit message summary line and any extended description that you want. Then click **Propose changes**, review your changes, and then create the PR.
    - When making the PR, make sure to specify the type of PR at the beginning of the PR's title (i.e. :bug: if it addresses a bug-type issue)

1. If the PR addresses a specific issue that has already been opened in GitHub, make sure to include the open issue number in **Related Issue(s)** (i.e. `Fixes #NNNN`); this will cause GitHub to automatically close the Issue once the PR is merged. If you have finished addressing an open issue without getting it automatically closed then explicitly close it.

## Repairing commits

If you have already created a PR that proposes to merge a branch that
adds commits that are not signed-off then you can repair this (and
lack of signing, if you choose) by adding the signoff to each using
`git commit -s --amend` on each of them. If you also want those
commits signed then you would use `git commit -s -S --amend` or
configure automatic signing. Following is an outline of how to do it
for a branch that adds **exactly one** commit. If your branch adds
more than one commit then you can extrapolate using `git cherry-pick -s -S`
(or `git rebase -i HEAD~NN` and setting commits to `edit`) to build up a
revised series of commits one-by-one.

The following instructions provide a basic walk-through if you have already created your own fork of the repository but yet not made a clone on your workstation.

1. Navigate to the **Code** page of the llm-d github.
   
2. Click the **Fork** dropdown in the top right corner of the page.
    - Under "Existing Forks" click your fork (should look something like "your_username/llm-d")
3. Once in your fork, click the **Code** dropdown.
    - Under the "Local" tab at the top of the dropdown, select the SSH tab
    - Copy the SSH repo URL to your clipboard
4. Open Git Bash (or your CLI of choice), create or change to a different directory if desired.
5. Clone the repository using `git clone` followed by pasting the URL you just copied.
6. Change your directory to the llm-d repo using `cd llm-d`.
7. `git checkout` to the branch in your fork where the changes were committed.
    - The branch name should be written at the top of your submitted PR page and looks something like "patch-*X*" (where "X" should be the number of PRs made on your fork to date)
8. Once in your branch, type `git commit -s --amend` to sign off your PR.
    - The commit will also be signed if either you have set up automatic signing or both include the `-S` flag on that command and have set up your SSH or GPG key.
    - You may extend that command with `-m` followed by a quoted commit message if you desire. Otherwise `git` will pop up an editor for you to use in making any desired adjustment to the commit message. After making any desired changes, save and exit the editor. FYI: in `vi` (which GitBash uses), when it is in Command mode (which is the normal mode, and contrasts with Insert mode) the keystrokes `:wq!` will attempt to save and then will exit no matter what.
9. Type `git push -f origin [branch_name]`, replacing `[branch_name]` with the actual name of your branch.
10. Navigate back to your PR github page.
    - A green `dco-signoff: yes` label indicates that your PR is successfully signed
