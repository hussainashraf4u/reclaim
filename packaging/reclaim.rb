# Homebrew formula template. Copy into a tap repo (homebrew-reclaim/Formula/reclaim.rb)
# and fill in url + sha256 after each PyPI release, e.g. with `brew update-python-resources`.
class Reclaim < Formula
  include Language::Python::Virtualenv

  desc "Find and safely clean reclaimable dev-machine data"
  homepage "https://github.com/OWNER/reclaim"
  url "https://files.pythonhosted.org/packages/source/r/reclaim/reclaim-0.2.0.tar.gz"
  sha256 "FILL_IN_AFTER_RELEASE"
  license "MIT"

  depends_on "python@3.12"

  # Run `brew update-python-resources reclaim` to generate the Textual dependency resources here.

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "reclaim", shell_output("#{bin}/reclaim --version")
  end
end
